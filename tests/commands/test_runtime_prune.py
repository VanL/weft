"""Tests for explicit runtime-state pruning."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import psutil
import pytest

from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    RUNTIME_PRUNE_CLASS_STALE_ENDPOINT,
    RUNTIME_PRUNE_CLASS_STALE_MANAGER,
    RUNTIME_PRUNE_CLASS_STALE_STREAMING,
    RUNTIME_PRUNE_CLASS_SUPERSEDED_ENDPOINT,
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
    run_runtime_prune,
    write_runtime_prune_report,
)
from weft.context import build_context
from weft.core import manager_runtime
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
    parse_service_owner_row,
)
from weft.ext import RunnerHandle
from weft.helpers import iter_queue_json_entries, reload_config, tid_short_form
from weft.liveness import registry

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
        queue=WEFT_ENDPOINTS_REGISTRY_QUEUE,
        queue_group="endpoints",
        message_id=1779400000000000001,
        key="1779400000000000002",
        classification=RUNTIME_PRUNE_CLASS_SUPERSEDED_ENDPOINT,
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


def test_tid_mapping_runtime_prune_group_is_rejected(tmp_path) -> None:
    ctx = _context(tmp_path)
    result = runtime_pruning.run_runtime_prune_for_context(
        ctx,
        RuntimePruneConfig(
            context_path=ctx.root,
            queues=("tid-mappings",),  # type: ignore[arg-type]
        ),
    )

    assert result.halted_at == "validation"
    assert result.errors == ("unsupported runtime queue group: tid-mappings",)


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
        queues=("managers",),
    )
    report_path = tmp_path / "runtime-report.jsonl"
    report_path.write_text("sentinel\n", encoding="utf-8")

    result = run_runtime_prune(config, report_path=report_path)

    assert result.errors == (
        f"failed to scan {WEFT_SERVICES_REGISTRY_QUEUE}: scan failed",
    )
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
            queues=("managers",),
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


@pytest.mark.parametrize("status", ["active", "draining", "stopped", "superseded"])
@pytest.mark.parametrize(
    "liveness,age,min_age,deleted",
    [
        ("live", 600, 0, False),
        ("live", 0, 0, False),
        ("stale", 60, 0, True),
        ("stale", 60, 120, False),
        ("unknown", 600, 0, True),
        ("unknown", 60, 0, False),
        ("unknown", 600, 900, False),
    ],
)
def test_manager_prune_requires_owner_evidence_and_both_age_windows(
    tmp_path, monkeypatch, status, liveness, age, min_age, deleted
) -> None:
    """Every status and history position obeys the same custody predicate."""
    ctx = _context(tmp_path)
    monkeypatch.setattr(registry, "_runtime_liveness_probes", {})
    observations = []

    def probe(handle, budget):
        observations.append(handle.id)
        return liveness

    registry.register_runtime_liveness_probe("prune-test", probe)
    handle = RunnerHandle(
        runner="prune-test",
        kind="supervised-process",
        id="owner",
        control={"authority": "external-supervisor"},
    )
    row_ids = [
        _write_json(
            ctx,
            WEFT_SERVICES_REGISTRY_QUEUE,
            _manager_service_payload(
                ctx,
                tid="1770000000000000020",
                status=status,
                runtime_handle=handle.to_dict(),
            ),
        )
        for _ in range(3)
    ]
    monkeypatch.setattr(
        runtime_pruning.time, "time_ns", lambda: max(row_ids) + age * 10**9
    )
    result = run_runtime_prune(
        RuntimePruneConfig(
            context_path=ctx.root,
            queues=("managers",),
            min_age_seconds=min_age,
            keep_recent_per_key=2,
            apply=True,
        )
    )
    assert result.errors == ()
    assert result.deleted == (3 if deleted else 0)
    assert {mid for _, mid in _read_rows(ctx, WEFT_SERVICES_REGISTRY_QUEUE)} == (
        set() if deleted else set(row_ids)
    )
    assert observations or age < min_age
    assert _read_rows(ctx, "T1770000000000000020.ctrl_in") == []


def test_manager_prune_preserves_live_host_identity_history(
    tmp_path, monkeypatch
) -> None:
    ctx = _context(tmp_path)
    handle = RunnerHandle(
        runner="host",
        kind="process",
        id=str(os.getpid()),
        control={"authority": "host-pid"},
        observations={
            "host_processes": [
                {"pid": os.getpid(), "create_time": psutil.Process().create_time()}
            ]
        },
    )
    row_ids = [
        _write_json(
            ctx,
            WEFT_SERVICES_REGISTRY_QUEUE,
            _manager_service_payload(
                ctx,
                tid="1770000000000000020",
                status="superseded",
                runtime_handle=handle.to_dict(),
            ),
        )
        for _ in range(3)
    ]
    monkeypatch.setattr(
        runtime_pruning.time, "time_ns", lambda: max(row_ids) + 600 * 10**9
    )
    result = run_runtime_prune(
        RuntimePruneConfig(
            context_path=ctx.root, queues=("managers",), min_age_seconds=0, apply=True
        )
    )
    assert result.deleted == 0
    assert len(_read_rows(ctx, WEFT_SERVICES_REGISTRY_QUEUE)) == 3


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
        _write_json(
            ctx, WEFT_TID_MAPPINGS_QUEUE, {"full": tid, "short": tid_short_form(tid)}
        )
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


@pytest.mark.parametrize(
    "age,min_age,deleted", [(60, 0, False), (600, 0, True), (600, 900, False)]
)
def test_managed_service_prune_uses_nanosecond_ttl(
    tmp_path, monkeypatch, age, min_age, deleted
) -> None:
    ctx = _context(tmp_path)
    mid = _write_json(
        ctx,
        WEFT_SERVICES_REGISTRY_QUEUE,
        _managed_service_payload(
            service_key="_weft.service.heartbeat", tid="1770000000000000100"
        ),
    )
    monkeypatch.setattr(runtime_pruning.time, "time_ns", lambda: mid + age * 10**9)
    result = run_runtime_prune(
        RuntimePruneConfig(
            context_path=ctx.root,
            queues=("services",),
            min_age_seconds=min_age,
            apply=True,
        )
    )
    assert result.errors == ()
    assert result.deleted == int(deleted)
    assert len(_read_rows(ctx, WEFT_SERVICES_REGISTRY_QUEUE)) == int(not deleted)


@pytest.mark.parametrize("logging_enabled", [False, True])
@pytest.mark.parametrize("already_missing", [False, True])
def test_malformed_service_prune_logs_only_actual_deletions(
    tmp_path, monkeypatch, caplog, logging_enabled, already_missing
) -> None:
    ctx = _context(tmp_path)
    try:
        with monkeypatch.context() as config_patch:
            config_patch.setenv("WEFT_LOGGING_ENABLED", "1" if logging_enabled else "0")
            reload_config()
            caplog.set_level(logging.ERROR)
            row_ids = [
                _write_json(
                    ctx,
                    WEFT_SERVICES_REGISTRY_QUEUE,
                    {
                        "schema": SERVICE_OWNER_SCHEMA,
                        "owner_tid": f"invalid-{index}",
                    },
                )
                for index in range(2)
            ]
            unknown_id = _write_json(
                ctx, WEFT_SERVICES_REGISTRY_QUEUE, {"schema": "weft.future-service.v99"}
            )
            config = RuntimePruneConfig(
                context_path=ctx.root, queues=("managers",), min_age_seconds=0
            )
            selected = run_runtime_prune(config).candidates
            if already_missing:
                queue = ctx.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
                try:
                    assert queue.delete(message_id=row_ids[0])
                finally:
                    queue.close()
            if already_missing:
                applied = runtime_pruning._apply_candidates(ctx, selected)
            else:
                result = run_runtime_prune(
                    RuntimePruneConfig(
                        context_path=ctx.root,
                        queues=("managers",),
                        min_age_seconds=0,
                        apply=True,
                    )
                )
                assert result.errors == ()
                assert result.failed == 0
                assert result.deleted == 2
                applied = result.applied_candidates
            expected_ids = set(row_ids[1:] if already_missing else row_ids)
            assert {c.message_id for c in applied if c.applied} == expected_ids
            assert all(c.error is None for c in applied)
            records = [
                r
                for r in caplog.records
                if r.getMessage() == "Pruned malformed service-owner row"
            ]
            assert {r.message_id for r in records} == (
                expected_ids if logging_enabled else set()
            )
            assert all(
                r.levelno == logging.ERROR
                and r.queue == WEFT_SERVICES_REGISTRY_QUEUE
                and r.owner_tid.startswith("invalid-")
                for r in records
            )
            assert {
                mid for _, mid in _read_rows(ctx, WEFT_SERVICES_REGISTRY_QUEUE)
            } == {unknown_id}
    finally:
        reload_config()


@pytest.mark.parametrize(
    "handle",
    [
        {},
        {"runner": "invalid"},
        {
            "runner": "host",
            "kind": "process",
            "id": "empty",
            "control": {"authority": "host-pid"},
            "observations": {"host_processes": []},
        },
    ],
)
@pytest.mark.parametrize(
    "age,min_age,deleted",
    [(299, 0, False), (300, 0, True), (599, 600, False), (600, 600, True)],
)
def test_valid_manager_row_with_missing_identity_obeys_both_prune_windows(
    tmp_path, monkeypatch, handle, age, min_age, deleted
) -> None:
    ctx = _context(tmp_path)
    payload = _manager_service_payload(
        ctx, tid="1770000000000000081", runtime_handle=handle
    )
    mid = _write_json(ctx, WEFT_SERVICES_REGISTRY_QUEUE, payload)
    assert parse_service_owner_row(payload, timestamp=mid).disposition == "accepted"
    record = manager_runtime.normalize_manager_registry_record(
        ctx, payload, timestamp=mid
    )
    assert record is not None
    assert manager_runtime.manager_registry_record_liveness(record) == "unknown"
    monkeypatch.setattr(runtime_pruning.time, "time_ns", lambda: mid + age * 10**9)
    result = run_runtime_prune(
        RuntimePruneConfig(
            context_path=ctx.root,
            queues=("managers",),
            min_age_seconds=min_age,
            apply=True,
        )
    )
    assert result.errors == ()
    assert result.deleted == int(deleted)
    assert [stamp for _row, stamp in _read_rows(ctx, WEFT_SERVICES_REGISTRY_QUEUE)] == (
        [] if deleted else [mid]
    )
    assert _read_rows(ctx, "T1770000000000000081.ctrl_in") == []
