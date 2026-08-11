"""Tests for explicit runtime-state pruning."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    RUNTIME_PRUNE_CLASS_STALE_ENDPOINT,
    RUNTIME_PRUNE_CLASS_STALE_MANAGER,
    RUNTIME_PRUNE_CLASS_STALE_STREAMING,
    RUNTIME_PRUNE_CLASS_SUPERSEDED_ENDPOINT,
    RUNTIME_PRUNE_CLASS_SUPERSEDED_MANAGER,
    RUNTIME_PRUNE_CLASS_SUPERSEDED_TID_MAPPING,
    RUNTIME_PRUNE_CLASS_UNSUPPORTED_PIPELINE,
    SERVICE_OWNER_SCHEMA,
    SERVICE_STATUS_ACTIVE,
    SERVICE_STATUS_TERMINAL,
    SERVICE_TYPE_MANAGED,
    WEFT_ENDPOINTS_REGISTRY_QUEUE,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_PIPELINES_STATE_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_STREAMING_SESSIONS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.commands import prune as prune_commands
from weft.commands.prune import (
    cmd_prune,
    run_runtime_prune,
    write_runtime_prune_report,
)
from weft.context import build_context
from weft.core.endpoints import build_endpoint_record_payload
from weft.core.pruning import runtime as runtime_pruning
from weft.core.pruning.runtime import (
    RuntimePruneCandidate,
    RuntimePruneConfig,
    RuntimePruneResult,
    RuntimeQueueScanStats,
)
from weft.core.service_convergence import (
    build_manager_service_payload,
    build_service_owner_payload,
)
from weft.ext import RunnerHandle
from weft.helpers import iter_queue_json_entries

pytestmark = [pytest.mark.shared]


def test_prune_command_does_not_reexport_core_config_types() -> None:
    assert not hasattr(prune_commands, "RuntimePruneConfig")
    assert not hasattr(prune_commands, "RetentionPruneConfig")


def test_runtime_prune_preserves_exact_run_id_format(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _context(tmp_path)
    monkeypatch.setattr(
        runtime_pruning.time,
        "strftime",
        lambda *_args: "2030-01-02T03:04:05",
    )
    monkeypatch.setattr(runtime_pruning.time, "time_ns", lambda: 9_876_543_210)
    monkeypatch.setattr(runtime_pruning.os, "getpid", lambda: 4321)

    result = runtime_pruning.run_runtime_prune_for_context(
        ctx,
        RuntimePruneConfig(context_path=ctx.root, queues=()),
    )

    assert result.run_id == "2030-01-02T03:04:05.876543210Z:pid-4321"


def test_runtime_prune_candidate_json_formats_message_id_only(tmp_path: Path) -> None:
    candidate = RuntimePruneCandidate(
        queue=WEFT_TID_MAPPINGS_QUEUE,
        queue_group="tid-mappings",
        message_id=1779400000000000001,
        key="1779400000000000002",
        classification=RUNTIME_PRUNE_CLASS_SUPERSEDED_TID_MAPPING,
        reason="superseded",
        age_seconds=3.0,
        payload_excerpt={"observed_at_ns": 1779400000000000003},
    )
    result = RuntimePruneResult(
        config=RuntimePruneConfig(),
        run_id="runtime-prune:test",
        candidates=(candidate,),
        applied_candidates=(),
        scan_stats=(RuntimeQueueScanStats(queue=WEFT_TID_MAPPINGS_QUEUE),),
    )

    report_path = tmp_path / "report.jsonl"
    write_runtime_prune_report(result, report_path)
    record = json.loads(report_path.read_text(encoding="utf-8").splitlines()[0])

    assert record["message_id"] == "1779400000000000001"
    assert record["payload_excerpt"]["observed_at_ns"] == 1779400000000000003
    assert isinstance(candidate.message_id, int)


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


def _manager_service_payload(
    ctx,
    *,
    tid: str,
    status: str = "active",
    name: str = "manager",
    runtime_handle: dict[str, object] | None = None,
) -> dict[str, object]:
    return build_manager_service_payload(
        context=ctx,
        tid=tid,
        name=name,
        status=status,
        queues={
            "requests": WEFT_SPAWN_REQUESTS_QUEUE,
            "ctrl_in": f"T{tid}.ctrl_in",
            "ctrl_out": f"T{tid}.ctrl_out",
            "outbox": WEFT_MANAGER_OUTBOX_QUEUE,
        },
        runtime_handle=runtime_handle or {},
    )


def _managed_service_payload(
    *,
    service_key: str,
    tid: str,
    status: str = SERVICE_STATUS_ACTIVE,
) -> dict[str, object]:
    return build_service_owner_payload(
        service_key=service_key,
        service_type=SERVICE_TYPE_MANAGED,
        owner_tid=tid,
        status=status,
        name="heartbeat-service"
        if status == SERVICE_STATUS_ACTIVE
        else "managed-service",
        queues={
            "ctrl_in": f"T{tid}.ctrl_in",
            "ctrl_out": f"T{tid}.ctrl_out",
            "inbox": f"T{tid}.inbox",
            "outbox": f"T{tid}.outbox",
        },
        runtime_handle={
            "runner": "host",
            "kind": "process",
            "id": tid[-4:],
            "control": {"authority": "host-pid"},
            "observations": {"host_pids": [int(tid[-4:])]},
        },
        metadata={"internal_role": "heartbeat"},
    )


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

    assert result.errors == ()
    assert result.failed == 0
    assert [(c.message_id, c.classification) for c in result.candidates] == [
        (old_id, RUNTIME_PRUNE_CLASS_SUPERSEDED_TID_MAPPING)
    ]
    rows = _read_rows(ctx, WEFT_TID_MAPPINGS_QUEUE)
    assert {message_id for _payload, message_id in rows} >= {old_id, new_id}


def test_runtime_selector_includes_candidate_at_exact_minimum_age(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _context(tmp_path)
    old_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "111", "full": "1770000000000000090", "name": "old"},
    )
    _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "222", "full": "1770000000000000090", "name": "new"},
    )
    monkeypatch.setattr(
        runtime_pruning.time,
        "time_ns",
        lambda: old_id + 5_000_000_000,
    )

    result = runtime_pruning.run_runtime_prune_for_context(
        ctx,
        RuntimePruneConfig(
            context_path=ctx.root,
            queues=("tid-mappings",),
            min_age_seconds=5.0,
        ),
    )

    assert [
        (candidate.message_id, candidate.age_seconds) for candidate in result.candidates
    ] == [(old_id, 5.0)]


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


def test_runtime_limit_applies_to_dry_run_and_apply_rescan(tmp_path) -> None:
    ctx = _context(tmp_path)
    full_tid = "1770000000000000091"
    oldest_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "111", "full": full_tid, "name": "oldest"},
    )
    middle_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "222", "full": full_tid, "name": "middle"},
    )
    latest_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "333", "full": full_tid, "name": "latest"},
    )
    config = RuntimePruneConfig(
        context_path=ctx.root,
        queues=("tid-mappings",),
        min_age_seconds=0,
        limit=1,
    )

    dry_run = runtime_pruning.run_runtime_prune_for_context(ctx, config)

    assert [candidate.message_id for candidate in dry_run.candidates] == [oldest_id]
    assert {
        message_id for _payload, message_id in _read_rows(ctx, WEFT_TID_MAPPINGS_QUEUE)
    } >= {
        oldest_id,
        middle_id,
        latest_id,
    }

    applied = runtime_pruning.run_runtime_prune_for_context(
        ctx,
        RuntimePruneConfig(
            context_path=ctx.root,
            queues=("tid-mappings",),
            min_age_seconds=0,
            limit=1,
            apply=True,
        ),
    )

    assert [candidate.message_id for candidate in applied.candidates] == [oldest_id]
    assert [candidate.message_id for candidate in applied.applied_candidates] == [
        oldest_id
    ]
    remaining_ids = {
        message_id for _payload, message_id in _read_rows(ctx, WEFT_TID_MAPPINGS_QUEUE)
    }
    assert oldest_id not in remaining_ids
    assert remaining_ids >= {middle_id, latest_id}


def test_runtime_apply_report_error_is_classified_after_delete(tmp_path) -> None:
    ctx = _context(tmp_path)
    old_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "111", "full": "1770000000000000003", "name": "old"},
    )
    new_id = _write_json(
        ctx,
        WEFT_TID_MAPPINGS_QUEUE,
        {"short": "222", "full": "1770000000000000003", "name": "new"},
    )
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("occupied", encoding="utf-8")

    exit_code, stdout, stderr = cmd_prune(
        family="runtime-state",
        context=ctx.root,
        apply=True,
        queues=("tid-mappings",),
        min_age_seconds=0,
        json_output=True,
        report_path=blocked_parent / "report.jsonl",
    )

    assert exit_code == 1
    assert json.loads(stdout)["deleted"] == 1
    assert stderr.startswith("failed to write report:")
    remaining_ids = {
        message_id for _payload, message_id in _read_rows(ctx, WEFT_TID_MAPPINGS_QUEUE)
    }
    assert old_id not in remaining_ids
    assert new_id in remaining_ids


def test_runtime_validation_error_does_not_create_or_truncate_report(
    tmp_path,
) -> None:
    ctx = _context(tmp_path)
    report_path = tmp_path / "runtime-report.jsonl"
    report_path.write_text("sentinel\n", encoding="utf-8")

    result = run_runtime_prune(
        RuntimePruneConfig(
            context_path=ctx.root,
            keep_recent_per_key=0,
        ),
        report_path=report_path,
    )

    assert result.errors == ("--keep-recent-per-key must be >= 1",)
    assert report_path.read_text(encoding="utf-8") == "sentinel\n"

    missing_report = tmp_path / "missing-runtime-report.jsonl"
    run_runtime_prune(
        RuntimePruneConfig(
            context_path=ctx.root,
            keep_recent_per_key=0,
        ),
        report_path=missing_report,
    )
    assert not missing_report.exists()


def test_runtime_initial_scan_error_does_not_create_or_truncate_report(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _context(tmp_path)

    def fail_scan(*_args, **_kwargs):
        raise RuntimeError("scan failed")

    monkeypatch.setattr(runtime_pruning, "_read_runtime_queue", fail_scan)
    config = RuntimePruneConfig(
        context_path=ctx.root,
        queues=("tid-mappings",),
    )
    report_path = tmp_path / "runtime-report.jsonl"
    report_path.write_text("sentinel\n", encoding="utf-8")

    result = run_runtime_prune(config, report_path=report_path)

    assert result.errors == (f"failed to scan {WEFT_TID_MAPPINGS_QUEUE}: scan failed",)
    assert report_path.read_text(encoding="utf-8") == "sentinel\n"

    missing_report = tmp_path / "missing-runtime-report.jsonl"
    run_runtime_prune(config, report_path=missing_report)
    assert not missing_report.exists()


def test_runtime_apply_rescan_error_writes_optional_report(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _context(tmp_path)
    calls = 0

    def build_candidates(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return ([], [], [] if calls == 1 else ["rescan failed"])

    monkeypatch.setattr(runtime_pruning, "_build_candidates", build_candidates)
    report_path = tmp_path / "runtime-report.jsonl"

    result = run_runtime_prune(
        RuntimePruneConfig(
            context_path=ctx.root,
            queues=("tid-mappings",),
            apply=True,
        ),
        report_path=report_path,
    )

    assert calls == 2
    assert result.errors == ("rescan failed",)
    records = [
        json.loads(line)
        for line in report_path.read_text(encoding="utf-8").splitlines()
    ]
    assert records[-1]["errors"] == ["rescan failed"]


def test_manager_prune_reports_superseded_and_stale_active_rows(tmp_path) -> None:
    ctx = _context(tmp_path)
    old_stopped = _write_json(
        ctx,
        WEFT_SERVICES_REGISTRY_QUEUE,
        _manager_service_payload(
            ctx,
            tid="1770000000000000010",
            status="stopped",
        ),
    )
    _write_json(
        ctx,
        WEFT_SERVICES_REGISTRY_QUEUE,
        _manager_service_payload(
            ctx,
            tid="1770000000000000010",
            status="stopped",
        ),
    )
    stale_active = _write_json(
        ctx,
        WEFT_SERVICES_REGISTRY_QUEUE,
        _manager_service_payload(ctx, tid="1770000000000000011"),
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
        WEFT_SERVICES_REGISTRY_QUEUE,
        _manager_service_payload(
            ctx,
            tid="1770000000000000012",
            runtime_handle=live_handle.to_dict(),
        ),
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
    assert (old_stopped, RUNTIME_PRUNE_CLASS_SUPERSEDED_MANAGER) in classifications
    assert (stale_active, RUNTIME_PRUNE_CLASS_STALE_MANAGER) in classifications
    assert all(candidate.message_id != live_active for candidate in result.candidates)


def test_manager_prune_honors_keep_recent_per_key(tmp_path) -> None:
    ctx = _context(tmp_path)
    tid = "1770000000000000020"
    old_id = _write_json(
        ctx,
        WEFT_SERVICES_REGISTRY_QUEUE,
        _manager_service_payload(ctx, tid=tid, status="stopped"),
    )
    middle_id = _write_json(
        ctx,
        WEFT_SERVICES_REGISTRY_QUEUE,
        _manager_service_payload(ctx, tid=tid, status="stopped"),
    )
    latest_id = _write_json(
        ctx,
        WEFT_SERVICES_REGISTRY_QUEUE,
        _manager_service_payload(ctx, tid=tid, status="stopped"),
    )

    result = run_runtime_prune(
        RuntimePruneConfig(
            context_path=ctx.root,
            queues=("managers",),
            min_age_seconds=0,
            keep_recent_per_key=2,
        )
    )

    candidate_ids = {candidate.message_id for candidate in result.candidates}
    assert old_id in candidate_ids
    assert middle_id not in candidate_ids
    assert latest_id not in candidate_ids


def test_manager_prune_reports_malformed_service_owner_rows(tmp_path) -> None:
    ctx = _context(tmp_path)
    malformed_id = _write_json(
        ctx,
        WEFT_SERVICES_REGISTRY_QUEUE,
        {
            "schema": SERVICE_OWNER_SCHEMA,
            "service_key": "bad",
            "service_type": "manager",
            "owner_tid": "not-a-tid",
            "status": "active",
        },
    )

    result = run_runtime_prune(
        RuntimePruneConfig(
            context_path=ctx.root,
            queues=("managers",),
            min_age_seconds=0,
        )
    )

    candidate = next(
        candidate
        for candidate in result.candidates
        if candidate.message_id == malformed_id
    )
    assert candidate.classification == RUNTIME_PRUNE_CLASS_STALE_MANAGER
    assert candidate.reason == "malformed_service_owner_row"


def test_services_prune_deletes_superseded_managed_service_history(tmp_path) -> None:
    ctx = _context(tmp_path)
    service_key = "_weft.service.heartbeat"
    first_active = _write_json(
        ctx,
        WEFT_SERVICES_REGISTRY_QUEUE,
        _managed_service_payload(
            service_key=service_key,
            tid="1770000000000000100",
            status=SERVICE_STATUS_ACTIVE,
        ),
    )
    first_terminal = _write_json(
        ctx,
        WEFT_SERVICES_REGISTRY_QUEUE,
        _managed_service_payload(
            service_key=service_key,
            tid="1770000000000000100",
            status=SERVICE_STATUS_TERMINAL,
        ),
    )
    second_active = _write_json(
        ctx,
        WEFT_SERVICES_REGISTRY_QUEUE,
        _managed_service_payload(
            service_key=service_key,
            tid="1770000000000000101",
            status=SERVICE_STATUS_ACTIVE,
        ),
    )

    result = run_runtime_prune(
        RuntimePruneConfig(
            context_path=ctx.root,
            queues=("services",),
            min_age_seconds=0,
            apply=True,
        )
    )

    assert result.errors == ()
    assert result.failed == 0
    assert result.deleted == 2
    deleted_ids = {candidate.message_id for candidate in result.applied_candidates}
    assert deleted_ids == {first_active, first_terminal}
    remaining_ids = {
        message_id
        for _payload, message_id in _read_rows(ctx, WEFT_SERVICES_REGISTRY_QUEUE)
    }
    assert first_active not in remaining_ids
    assert first_terminal not in remaining_ids
    assert second_active in remaining_ids


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
        (stale_id, RUNTIME_PRUNE_CLASS_STALE_STREAMING)
    ]
    remaining_ids = {
        message_id
        for _payload, message_id in _read_rows(ctx, WEFT_STREAMING_SESSIONS_QUEUE)
    }
    assert stale_id not in remaining_ids
    assert active_id in remaining_ids


def test_streaming_prune_preserves_duplicate_marker_for_running_owner(tmp_path) -> None:
    ctx = _context(tmp_path)
    tid = "1770000000000000022"
    older_id = _write_json(
        ctx,
        WEFT_STREAMING_SESSIONS_QUEUE,
        {"tid": tid, "session_id": "live-duplicate", "queue": f"T{tid}.outbox"},
    )
    newer_id = _write_json(
        ctx,
        WEFT_STREAMING_SESSIONS_QUEUE,
        {"tid": tid, "session_id": "live-duplicate", "queue": f"T{tid}.outbox"},
    )
    _write_json(ctx, WEFT_GLOBAL_LOG_QUEUE, {"tid": tid, "status": "running"})

    result = run_runtime_prune(
        RuntimePruneConfig(
            context_path=ctx.root,
            queues=("streaming",),
            min_age_seconds=0,
            apply=True,
        )
    )

    assert result.candidates == ()
    remaining_ids = {
        message_id
        for _payload, message_id in _read_rows(ctx, WEFT_STREAMING_SESSIONS_QUEUE)
    }
    assert older_id in remaining_ids
    assert newer_id in remaining_ids


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
    assert (old_id, RUNTIME_PRUNE_CLASS_SUPERSEDED_ENDPOINT) in classifications
    assert (stale_owner, RUNTIME_PRUNE_CLASS_STALE_ENDPOINT) in classifications
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
    ] == [(pipeline_id, RUNTIME_PRUNE_CLASS_UNSUPPORTED_PIPELINE, True)]
    assert result.deleted == 0
    remaining_ids = {
        message_id
        for _payload, message_id in _read_rows(ctx, WEFT_PIPELINES_STATE_QUEUE)
    }
    assert pipeline_id in remaining_ids
