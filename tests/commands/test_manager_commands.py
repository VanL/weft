"""Tests for manager CLI command helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from simplebroker import Queue
from simplebroker.ext import BrokerError
from tests.helpers.test_backend import prepare_project_root
from weft._constants import (
    MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS,
    MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
    SERVICE_STATUS_SUPERSEDED,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
)
from weft._exceptions import ManagerNotRunning
from weft.commands import manager as manager_cmd
from weft.commands.types import ManagerSnapshot
from weft.context import build_context
from weft.core import manager_runtime as core_manager_runtime
from weft.core.control_messages import encode_control_message
from weft.core.control_probe import ControlProbeResult, MatchedPong
from weft.core.service_convergence import (
    build_manager_service_payload,
    manager_service_key,
    project_manager_service_record,
)
from weft.helpers import iter_queue_json_entries

pytestmark = [pytest.mark.shared]


def test_manager_runtime_exposes_only_canonical_lifecycle_names() -> None:
    """Legacy private twins must not coexist with the public runtime API."""

    canonical_names = {
        "DetachedManagerLaunch",
        "ManagerRegistryView",
        "ManagerRuntimeInvocation",
        "build_manager_spec",
        "ensure_manager",
        "generate_tid",
        "list_manager_records",
        "manager_diagnostic_records",
        "manager_record",
        "manager_registry_record_is_stale",
        "normalize_manager_registry_record",
        "replace_active_manager",
        "select_active_manager",
        "serve_manager_foreground",
        "start_manager",
        "stop_manager",
    }
    legacy_names = {
        "_DetachedManagerLaunch",
        "_ManagerRegistryView",
        "_ManagerRuntimeInvocation",
        "_build_manager_spec",
        "_ensure_manager",
        "_generate_tid",
        "_list_manager_records",
        "_manager_diagnostic_records",
        "_manager_record",
        "_manager_record_is_stale",
        "_normalize_manager_record",
        "_replace_active_manager",
        "_select_active_manager",
        "_serve_manager_foreground",
        "_start_manager",
        "_stop_manager",
    }

    assert all(hasattr(core_manager_runtime, name) for name in canonical_names)
    assert not {name for name in legacy_names if hasattr(core_manager_runtime, name)}


class _CleanupProcess:
    def __init__(self, *, failure_phase: str, error: BaseException) -> None:
        self.failure_phase = failure_phase
        self.error = error
        self.calls: list[str] = []

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        self.calls.append("terminate")
        if self.failure_phase == "terminate":
            raise self.error

    def wait(self, *, timeout: float) -> None:
        self.calls.append(f"wait:{timeout}")
        raise subprocess.TimeoutExpired("manager", timeout)

    def kill(self) -> None:
        self.calls.append("kill")
        if self.failure_phase == "kill":
            raise self.error


@pytest.mark.parametrize("failure_phase", ["terminate", "kill"])
def test_terminate_manager_process_contains_os_cleanup_failure(
    failure_phase: str,
) -> None:
    process = _CleanupProcess(failure_phase=failure_phase, error=OSError("wait failed"))

    core_manager_runtime._terminate_manager_process(process, timeout=0.25)  # type: ignore[arg-type]

    if failure_phase == "terminate":
        assert process.calls == ["terminate"]
    else:
        assert process.calls == ["terminate", "wait:0.25", "kill"]


@pytest.mark.parametrize("failure_phase", ["terminate", "kill"])
def test_terminate_manager_process_propagates_unexpected_cleanup_defect(
    failure_phase: str,
) -> None:
    process = _CleanupProcess(
        failure_phase=failure_phase,
        error=RuntimeError("cleanup defect"),
    )

    with pytest.raises(RuntimeError, match="cleanup defect"):
        core_manager_runtime._terminate_manager_process(process, timeout=0.25)  # type: ignore[arg-type]


def test_manager_snapshot_converts_every_public_field() -> None:
    runtime_handle = {
        "runner": "host",
        "kind": "process",
        "id": "42",
        "control": {"authority": "host-pid"},
    }
    record = {
        "tid": 1761000000000000000,
        "status": "active",
        "name": "primary",
        "runtime_handle": runtime_handle,
        "timestamp": "1761000000000000123",
        "role": "manager",
        "requests": "requests",
        "internal_requests": "internal-requests",
        "internal_reserved": "internal-reserved",
        "outbox": "outbox",
        "ctrl_in": "ctrl-in",
        "ctrl_out": "ctrl-out",
    }

    snapshot = manager_cmd._manager_snapshot(record)

    assert snapshot == ManagerSnapshot(
        tid="1761000000000000000",
        status="active",
        name="primary",
        runtime_handle=runtime_handle,
        timestamp=1761000000000000123,
        role="manager",
        requests="requests",
        internal_requests="internal-requests",
        internal_reserved="internal-reserved",
        outbox="outbox",
        ctrl_in="ctrl-in",
        ctrl_out="ctrl-out",
    )
    assert snapshot.runtime_handle is not runtime_handle


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        (123, 123),
        (123.0, None),
        ("123", 123),
        ("12.5", None),
        ("not-a-timestamp", None),
        (None, None),
    ],
)
def test_manager_snapshot_coerces_only_numeric_timestamps(
    timestamp: object,
    expected: int | None,
) -> None:
    snapshot = manager_cmd._manager_snapshot({"timestamp": timestamp})

    assert snapshot.timestamp == expected


def test_manager_snapshot_discards_malformed_optional_fields() -> None:
    snapshot = manager_cmd._manager_snapshot(
        {
            "runtime_handle": ["not", "a", "mapping"],
            "role": 1,
            "requests": {},
            "internal_requests": [],
            "internal_reserved": 2,
            "outbox": False,
            "ctrl_in": object(),
            "ctrl_out": (),
        }
    )

    assert snapshot == ManagerSnapshot(
        tid="",
        status="unknown",
        name="",
        runtime_handle=None,
        timestamp=None,
    )


def test_cmd_manager_list_returns_lossless_diagnostic_snapshots(
    tmp_path, monkeypatch
) -> None:
    """Diagnostic mode returns structured proof fields without rendering."""

    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)
    monkeypatch.setattr(
        core_manager_runtime,
        "manager_diagnostic_records",
        lambda context_arg, *, include_stopped: [
            {
                "tid": "1761000000000000001",
                "status": "active",
                "name": "manager",
                "liveness": "live",
                "proof_source": "pong",
                "proof_detail": "matched nonce",
                "dispatch_eligible": True,
                "canonical_candidate": True,
                "canonical": True,
            }
        ],
    )

    result = manager_cmd.cmd_manager_list(
        all=False,
        diagnostic=True,
        context=context_root,
    )

    assert result == (
        ManagerSnapshot(
            tid="1761000000000000001",
            status="active",
            name="manager",
            runtime_handle=None,
            timestamp=None,
            liveness="live",
            proof_source="pong",
            proof_detail="matched nonce",
            dispatch_eligible=True,
            canonical_candidate=True,
            canonical=True,
        ),
    )


def test_cmd_manager_start_returns_structured_snapshot(tmp_path, monkeypatch) -> None:
    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    record = {
        "tid": "1761000000000000002",
        "status": "active",
        "name": "manager",
    }
    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)
    monkeypatch.setattr(
        core_manager_runtime,
        "ensure_manager",
        lambda context_arg: (record, True, None),
    )

    result = manager_cmd.cmd_manager_start(context=context_root)

    assert result.tid == "1761000000000000002"
    assert result.status == "active"


def test_cmd_manager_stop_returns_none_when_active_manager_is_absent(
    tmp_path, monkeypatch
) -> None:
    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)
    monkeypatch.setattr(
        core_manager_runtime,
        "select_active_manager",
        lambda context_arg, *, probe_stale, probe_cache: None,
    )

    assert manager_cmd.cmd_manager_stop(context=context_root) is None


def test_cmd_manager_status_raises_typed_error_when_absent(
    tmp_path, monkeypatch
) -> None:
    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)
    monkeypatch.setattr(
        core_manager_runtime,
        "manager_record",
        lambda context_arg, tid: None,
    )

    with pytest.raises(ManagerNotRunning, match="not found"):
        manager_cmd.cmd_manager_status("1761000000000000003", context=context_root)


def test_manager_json_projection_formats_only_owned_broker_ids() -> None:
    first_id = 1_779_300_000_000_000_001
    second_id = 1_779_300_000_000_000_002
    record = {
        "tid": "1779300000000000100",
        "timestamp": first_id,
        "_pong_live_at": second_id,
        "metadata": {
            "supersession_observed_timestamp": first_id,
            "opaque_timestamp": second_id,
        },
        "_service_owner_payload": {"timestamp": second_id},
    }

    projected = manager_cmd._manager_record_to_json(record)

    assert projected["timestamp"] == "1779300000000000001"
    assert projected["_pong_live_at"] == "1779300000000000002"
    assert projected["metadata"] == {
        "supersession_observed_timestamp": "1779300000000000001",
        "opaque_timestamp": second_id,
    }
    assert projected["_service_owner_payload"] == {"timestamp": second_id}
    assert record["timestamp"] == first_id
    assert record["_pong_live_at"] == second_id


def test_manager_json_commands_use_external_id_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message_id = 1_779_300_000_000_000_003
    record = {
        "tid": "1779300000000000101",
        "status": "active",
        "timestamp": message_id,
    }
    monkeypatch.setattr(manager_cmd, "build_context", lambda _path=None: object())
    monkeypatch.setattr(
        core_manager_runtime,
        "list_manager_records",
        lambda *_args, **_kwargs: [record],
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "manager_record",
        lambda *_args, **_kwargs: record,
    )

    list_exit, list_payload = manager_cmd.list_command(json_output=True)
    status_exit, status_payload = manager_cmd.status_command(
        tid=record["tid"],
        json_output=True,
    )

    assert list_exit == 0
    assert status_exit == 0
    assert json.loads(list_payload or "[]")[0]["timestamp"] == ("1779300000000000003")
    assert json.loads(status_payload or "{}")["timestamp"] == ("1779300000000000003")
    assert record["timestamp"] == message_id


def _host_runtime_handle(pid: int) -> dict[str, object]:
    return {
        "runner": "host",
        "kind": "process",
        "id": str(pid),
        "control": {"authority": "host-pid"},
        "observations": {"host_pids": [pid]},
        "metadata": {},
    }


def _external_supervisor_runtime_handle() -> dict[str, object]:
    return {
        "runner": "manager-supervisor",
        "kind": "supervised-process",
        "id": "container:weft-manager-1",
        "control": {"authority": "external-supervisor"},
        "observations": {"container_pid": 1, "container_name": "weft-manager-1"},
        "metadata": {},
    }


def _manager_service_payload(
    context,
    tid: str,
    *,
    status: str = "active",
    name: str = "manager",
    runtime_handle: dict[str, object] | None = None,
    ctrl_in: str | None = None,
    ctrl_out: str | None = None,
    outbox: str = "weft.manager.outbox",
    requests: str = WEFT_SPAWN_REQUESTS_QUEUE,
) -> dict[str, object]:
    return build_manager_service_payload(
        context=context,
        tid=tid,
        name=name,
        status=status,
        queues={
            "requests": requests,
            "ctrl_in": ctrl_in or f"T{tid}.ctrl_in",
            "ctrl_out": ctrl_out or f"T{tid}.ctrl_out",
            "outbox": outbox,
        },
        runtime_handle=runtime_handle or {},
    )


def _latest_manager_record(context, tid: str) -> dict[str, object] | None:
    queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    try:
        latest: tuple[dict[str, object], int] | None = None
        for payload, timestamp in iter_queue_json_entries(queue):
            record = project_manager_service_record(
                payload,
                timestamp=timestamp,
                service_key=manager_service_key(context),
            )
            if record is None or record.get("tid") != tid:
                continue
            if latest is None or latest[1] < timestamp:
                latest = (record, timestamp)
        return None if latest is None else latest[0]
    finally:
        queue.close()


def _manager_registry_rows(queue: Queue) -> list[tuple[dict[str, Any], int]]:
    return list(iter_queue_json_entries(queue))


def _write_manager_registry_row(
    queue: Queue,
    context: Any,
    tid: str,
    **overrides: Any,
) -> int:
    return queue.write(json.dumps(_manager_service_payload(context, tid, **overrides)))


def _manager_service_record(context: Any, tid: str) -> dict[str, Any]:
    payload = _manager_service_payload(context, tid)
    record = project_manager_service_record(payload, timestamp=1)
    assert record is not None
    return record


@dataclass(frozen=True, slots=True)
class _ManagerSnapshotCase:
    name: str
    status: str = "active"
    canonical: bool = True
    prune_stale: bool = True
    probe_stale: bool = False
    stale_result: tuple[bool, bool] | None = (True, False)
    pong_result: bool | None = False
    namespace_ambiguous: bool = False
    expected_in_view: bool = False
    expected_deleted: bool = False
    expected_pong_calls: int = 1


_MANAGER_SNAPSHOT_CASES = (
    _ManagerSnapshotCase(
        "non-active",
        status="stopped",
        probe_stale=True,
        stale_result=None,
        pong_result=None,
        expected_in_view=True,
        expected_pong_calls=0,
    ),
    _ManagerSnapshotCase(
        "active-live",
        probe_stale=True,
        stale_result=(False, False),
        pong_result=None,
        expected_in_view=True,
        expected_pong_calls=0,
    ),
    _ManagerSnapshotCase(
        "definitive-stale-probe-disabled",
        stale_result=(True, True),
        pong_result=None,
        expected_deleted=True,
        expected_pong_calls=0,
    ),
    _ManagerSnapshotCase(
        "definitive-stale-probe-enabled",
        probe_stale=True,
        stale_result=(True, True),
        pong_result=None,
        expected_deleted=True,
        expected_pong_calls=0,
    ),
    _ManagerSnapshotCase(
        "canonical-pong-rescue-probe-disabled",
        pong_result=True,
        expected_in_view=True,
    ),
    _ManagerSnapshotCase(
        "canonical-pong-rescue-probe-enabled",
        probe_stale=True,
        pong_result=True,
        expected_in_view=True,
    ),
    _ManagerSnapshotCase("canonical-unmatched-pong-omit-only"),
    _ManagerSnapshotCase(
        "canonical-unmatched-pong-prune",
        probe_stale=True,
        expected_deleted=True,
    ),
    _ManagerSnapshotCase(
        "noncanonical-omit-only-without-pong",
        canonical=False,
        pong_result=None,
        expected_pong_calls=0,
    ),
    _ManagerSnapshotCase(
        "noncanonical-prune-without-pong",
        canonical=False,
        probe_stale=True,
        pong_result=None,
        expected_deleted=True,
        expected_pong_calls=0,
    ),
    _ManagerSnapshotCase(
        "canonical-ambiguous-keep-probe-disabled",
        namespace_ambiguous=True,
        expected_in_view=True,
    ),
    _ManagerSnapshotCase(
        "canonical-ambiguous-keep-probe-enabled",
        probe_stale=True,
        namespace_ambiguous=True,
        expected_in_view=True,
    ),
    _ManagerSnapshotCase(
        "noncanonical-ambiguous-keep-probe-disabled",
        canonical=False,
        pong_result=None,
        namespace_ambiguous=True,
        expected_in_view=True,
        expected_pong_calls=0,
    ),
    _ManagerSnapshotCase(
        "noncanonical-ambiguous-keep-probe-enabled",
        canonical=False,
        probe_stale=True,
        pong_result=None,
        namespace_ambiguous=True,
        expected_in_view=True,
        expected_pong_calls=0,
    ),
    _ManagerSnapshotCase(
        "pruning-disabled",
        prune_stale=False,
        probe_stale=True,
        stale_result=None,
        pong_result=None,
        expected_in_view=True,
        expected_pong_calls=0,
    ),
)


@pytest.mark.parametrize(
    "case",
    _MANAGER_SNAPSHOT_CASES,
    ids=lambda case: case.name,
)
def test_snapshot_registry_decision_table_uses_one_record_evidence_frame(
    tmp_path,
    monkeypatch,
    case: _ManagerSnapshotCase,
) -> None:
    context = build_context(prepare_project_root(tmp_path / "ctx"))
    tid = "1761000000000000100"
    queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    stale_calls: list[str] = []
    pong_calls: list[str] = []

    def stale_status(record: dict[str, Any]) -> tuple[bool, bool]:
        stale_calls.append(str(record["tid"]))
        if case.stale_result is None:
            pytest.fail("stale classification must be skipped for this row")
        return case.stale_result

    def matched_pong(
        _context: Any,
        record: dict[str, Any],
        *,
        probe_cache: dict[str, int | None] | None,
    ) -> bool:
        del probe_cache
        pong_calls.append(str(record["tid"]))
        if case.pong_result is None:
            pytest.fail("PONG probing must be skipped for this row")
        return case.pong_result

    def namespace_ambiguous(record: dict[str, Any]) -> bool:
        del record
        return case.namespace_ambiguous

    monkeypatch.setattr(
        core_manager_runtime,
        "_manager_record_stale_status",
        stale_status,
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_manager_record_has_matched_pong",
        matched_pong,
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_host_pid_visibility_is_namespace_ambiguous",
        namespace_ambiguous,
    )

    try:
        message_id = _write_manager_registry_row(
            queue,
            context,
            tid,
            status=case.status,
            runtime_handle=_host_runtime_handle(987654321),
            requests=(
                WEFT_SPAWN_REQUESTS_QUEUE
                if case.canonical
                else "custom.manager.requests"
            ),
        )

        snapshot = core_manager_runtime._snapshot_registry(
            context,
            prune_stale=case.prune_stale,
            probe_stale=case.probe_stale,
            probe_cache={},
            queue=queue,
        )
        remaining_ids = [
            timestamp for _payload, timestamp in _manager_registry_rows(queue)
        ]
    finally:
        queue.close()

    assert (tid in snapshot) is case.expected_in_view
    if case.expected_in_view:
        assert snapshot[tid]["timestamp"] == message_id
    assert remaining_ids == ([] if case.expected_deleted else [message_id])
    assert len(stale_calls) == (1 if case.stale_result is not None else 0)
    assert pong_calls == [tid] * case.expected_pong_calls


@pytest.mark.parametrize(
    "reverse_input",
    [False, True],
    ids=["chronological-input", "reversed-input"],
)
def test_snapshot_registry_latest_included_timestamp_wins(
    tmp_path,
    monkeypatch,
    *,
    reverse_input: bool,
) -> None:
    context = build_context(prepare_project_root(tmp_path / "ctx"))
    tid = "1761000000000000101"
    queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    monkeypatch.setattr(
        core_manager_runtime,
        "_manager_record_stale_status",
        lambda _record: (False, False),
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_manager_record_has_matched_pong",
        lambda *_args, **_kwargs: pytest.fail("live rows must not be PONG-probed"),
    )

    try:
        first_id = _write_manager_registry_row(
            queue,
            context,
            tid,
            name="first",
            runtime_handle=_host_runtime_handle(987654321),
        )
        second_id = _write_manager_registry_row(
            queue,
            context,
            tid,
            name="second",
            runtime_handle=_host_runtime_handle(987654321),
        )
        rows = _manager_registry_rows(queue)

        def ordered_rows(
            registry_queue: Queue,
        ) -> Iterator[tuple[dict[str, Any], int]]:
            assert registry_queue is queue
            return iter(reversed(rows) if reverse_input else rows)

        monkeypatch.setattr(
            core_manager_runtime,
            "iter_queue_json_entries",
            ordered_rows,
        )

        snapshot = core_manager_runtime._snapshot_registry(context, queue=queue)
        remaining_ids = [
            timestamp for _payload, timestamp in _manager_registry_rows(queue)
        ]
    finally:
        queue.close()

    assert first_id < second_id
    assert snapshot[tid]["name"] == "second"
    assert snapshot[tid]["timestamp"] == second_id
    assert remaining_ids == [first_id, second_id]


@pytest.mark.parametrize(
    ("probe_stale", "newer_deleted"),
    [(False, False), (True, True)],
    ids=["newer-omitted", "newer-pruned"],
)
def test_snapshot_registry_newer_filtered_row_preserves_older_included_row(
    tmp_path,
    monkeypatch,
    probe_stale: bool,
    newer_deleted: bool,
) -> None:
    context = build_context(prepare_project_root(tmp_path / "ctx"))
    tid = "1761000000000000102"
    queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    pong_calls: list[str] = []

    def stale_status(record: dict[str, Any]) -> tuple[bool, bool]:
        return record["name"] == "newer", False

    def no_matched_pong(
        _context: Any,
        record: dict[str, Any],
        *,
        probe_cache: dict[str, int | None] | None,
    ) -> bool:
        del probe_cache
        pong_calls.append(str(record["name"]))
        return False

    monkeypatch.setattr(
        core_manager_runtime,
        "_manager_record_stale_status",
        stale_status,
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_manager_record_has_matched_pong",
        no_matched_pong,
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_host_pid_visibility_is_namespace_ambiguous",
        lambda _record: False,
    )

    try:
        older_id = _write_manager_registry_row(
            queue,
            context,
            tid,
            name="older",
            runtime_handle=_host_runtime_handle(987654321),
        )
        newer_id = _write_manager_registry_row(
            queue,
            context,
            tid,
            name="newer",
            runtime_handle=_host_runtime_handle(987654321),
        )

        snapshot = core_manager_runtime._snapshot_registry(
            context,
            probe_stale=probe_stale,
            probe_cache={},
            queue=queue,
        )
        remaining_ids = [
            timestamp for _payload, timestamp in _manager_registry_rows(queue)
        ]
    finally:
        queue.close()

    assert snapshot[tid]["name"] == "older"
    assert snapshot[tid]["timestamp"] == older_id
    assert remaining_ids == ([older_id] if newer_deleted else [older_id, newer_id])
    assert pong_calls == ["newer"]


def test_snapshot_registry_does_not_close_caller_owned_queue(
    tmp_path,
    monkeypatch,
) -> None:
    context = build_context(prepare_project_root(tmp_path / "ctx"))
    tid = "1761000000000000103"
    queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    original_close: Callable[[], None] = queue.close
    close_calls: list[None] = []

    def track_close() -> None:
        close_calls.append(None)

    monkeypatch.setattr(queue, "close", track_close)
    try:
        first_id = _write_manager_registry_row(
            queue,
            context,
            tid,
            status="stopped",
        )

        snapshot = core_manager_runtime._snapshot_registry(
            context,
            prune_stale=False,
            queue=queue,
        )
        second_id = _write_manager_registry_row(
            queue,
            context,
            "1761000000000000104",
            status="stopped",
        )
        remaining_ids = [
            timestamp for _payload, timestamp in _manager_registry_rows(queue)
        ]
    finally:
        original_close()

    assert snapshot[tid]["timestamp"] == first_id
    assert close_calls == []
    assert remaining_ids == [first_id, second_id]


def test_snapshot_registry_closes_locally_acquired_queue(
    tmp_path,
    monkeypatch,
) -> None:
    context = build_context(prepare_project_root(tmp_path / "ctx"))
    tid = "1761000000000000105"
    queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    original_close: Callable[[], None] = queue.close
    close_calls: list[None] = []

    def track_close() -> None:
        close_calls.append(None)
        original_close()

    _write_manager_registry_row(queue, context, tid, status="stopped")
    monkeypatch.setattr(queue, "close", track_close)
    monkeypatch.setattr(core_manager_runtime, "_registry_queue", lambda _context: queue)

    snapshot = core_manager_runtime._snapshot_registry(
        context,
        prune_stale=False,
    )

    assert snapshot[tid]["status"] == "stopped"
    assert close_calls == [None]


@pytest.mark.parametrize(
    "error",
    [
        BrokerError("broker delete failed"),
        OSError("OS delete failed"),
        RuntimeError("delete failed"),
    ],
    ids=["broker-error", "os-error", "runtime-error"],
)
def test_snapshot_registry_operational_delete_failure_continues_later_deletes(
    tmp_path,
    monkeypatch,
    error: Exception,
) -> None:
    context = build_context(prepare_project_root(tmp_path / "ctx"))
    queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    original_delete: Callable[..., bool] = queue.delete
    original_close: Callable[[], None] = queue.close
    delete_attempts: list[int] = []

    monkeypatch.setattr(
        core_manager_runtime,
        "_manager_record_stale_status",
        lambda _record: (True, True),
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_host_pid_visibility_is_namespace_ambiguous",
        lambda _record: False,
    )

    try:
        first_id = _write_manager_registry_row(
            queue,
            context,
            "1761000000000000106",
            runtime_handle=_host_runtime_handle(987654321),
        )
        second_id = _write_manager_registry_row(
            queue,
            context,
            "1761000000000000107",
            runtime_handle=_host_runtime_handle(987654321),
        )

        def fail_first_delete(*, message_id: int | str | None = None) -> bool:
            assert message_id is not None
            timestamp = int(message_id)
            delete_attempts.append(timestamp)
            if timestamp == first_id:
                raise error
            return original_delete(message_id=message_id)

        monkeypatch.setattr(queue, "delete", fail_first_delete)

        snapshot = core_manager_runtime._snapshot_registry(context, queue=queue)
        remaining_ids = [
            timestamp for _payload, timestamp in _manager_registry_rows(queue)
        ]
    finally:
        original_close()

    assert snapshot == {}
    assert delete_attempts == [first_id, second_id]
    assert remaining_ids == [first_id]


def test_snapshot_registry_propagates_unexpected_delete_defect(
    tmp_path,
    monkeypatch,
) -> None:
    context = build_context(prepare_project_root(tmp_path / "ctx"))
    tid = "1761000000000000108"
    queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    original_close: Callable[[], None] = queue.close
    monkeypatch.setattr(
        core_manager_runtime,
        "_manager_record_stale_status",
        lambda _record: (True, True),
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_host_pid_visibility_is_namespace_ambiguous",
        lambda _record: False,
    )

    try:
        message_id = _write_manager_registry_row(
            queue,
            context,
            tid,
            runtime_handle=_host_runtime_handle(987654321),
        )

        def defective_delete(*, message_id: int | str | None = None) -> bool:
            del message_id
            raise ValueError("unexpected delete defect")

        monkeypatch.setattr(queue, "delete", defective_delete)

        with pytest.raises(ValueError, match="unexpected delete defect"):
            core_manager_runtime._snapshot_registry(context, queue=queue)
        remaining_ids = [
            timestamp for _payload, timestamp in _manager_registry_rows(queue)
        ]
    finally:
        original_close()

    assert remaining_ids == [message_id]


@pytest.mark.parametrize(
    ("pong_kind", "expected_in_view"),
    [
        ("absent", False),
        ("malformed-manager-fields", False),
        ("mismatched-control", False),
        ("matched", True),
    ],
)
def test_snapshot_registry_accepts_only_dispatch_eligible_matched_pong(
    tmp_path,
    monkeypatch,
    pong_kind: str,
    expected_in_view: bool,
) -> None:
    context = build_context(prepare_project_root(tmp_path / "ctx"))
    tid = "1761000000000000109"
    queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    probe_calls: list[tuple[str, str, str]] = []
    probe_cache: dict[str, int | None] = {}
    observed_at = 1761000000000000199

    monkeypatch.setattr(
        core_manager_runtime,
        "_manager_record_stale_status",
        lambda _record: (True, False),
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_host_pid_visibility_is_namespace_ambiguous",
        lambda _record: False,
    )

    def send_probe(
        _context: Any,
        *,
        tid: str,
        ctrl_in_name: str,
        ctrl_out_name: str,
        timeout: float,
        request_id: str | None = None,
    ) -> ControlProbeResult:
        del timeout, request_id
        probe_calls.append((tid, ctrl_in_name, ctrl_out_name))
        if pong_kind == "absent":
            return ControlProbeResult(request_id="probe", timed_out=True)
        payload: dict[str, Any] = {
            "command": "PING",
            "status": "ok",
            "message": "PONG",
            "request_id": "probe",
            "tid": tid,
            "task_status": "running",
            "role": "manager",
            "requests": WEFT_SPAWN_REQUESTS_QUEUE,
            "ctrl_in": ctrl_in_name,
            "ctrl_out": ctrl_out_name,
            "outbox": "weft.manager.outbox",
            "weft_context": str(context.root),
            "should_stop": False,
        }
        if pong_kind == "malformed-manager-fields":
            payload["role"] = 1
        elif pong_kind == "mismatched-control":
            payload["ctrl_out"] = "Tother.ctrl_out"
        return ControlProbeResult(
            request_id="probe",
            matched=MatchedPong(
                payload=payload,
                observed_at=observed_at,
                request_id="probe",
            ),
        )

    monkeypatch.setattr(core_manager_runtime, "send_keyed_ping_probe", send_probe)

    try:
        message_id = _write_manager_registry_row(
            queue,
            context,
            tid,
            runtime_handle=_host_runtime_handle(987654321),
        )
        snapshot = core_manager_runtime._snapshot_registry(
            context,
            probe_stale=False,
            probe_cache=probe_cache,
            queue=queue,
        )
        remaining_ids = [
            timestamp for _payload, timestamp in _manager_registry_rows(queue)
        ]
    finally:
        queue.close()

    assert (tid in snapshot) is expected_in_view
    assert probe_calls == [(tid, f"T{tid}.ctrl_in", f"T{tid}.ctrl_out")]
    assert probe_cache == {tid: observed_at if expected_in_view else None}
    assert remaining_ids == [message_id]


def test_start_command_delegates_to_shared_bootstrap(tmp_path, monkeypatch):
    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    calls: list[str] = []

    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)

    def _fake_ensure(context_arg):
        assert context_arg is context
        calls.append("ensure")
        return (
            {
                "tid": "1761000000000000000",
                "runtime_handle": _host_runtime_handle(12345),
            },
            True,
            None,
        )

    monkeypatch.setattr(core_manager_runtime, "ensure_manager", _fake_ensure)

    exit_code, message = manager_cmd.start_command(context_path=context_root)

    assert exit_code == 0
    assert message == "Started manager 1761000000000000000"
    assert calls == ["ensure"]


def test_start_command_reports_existing_manager(tmp_path, monkeypatch):
    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)

    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)
    monkeypatch.setattr(
        core_manager_runtime,
        "ensure_manager",
        lambda context_arg: (
            {
                "tid": "1761000000000000001",
                "runtime_handle": _host_runtime_handle(54321),
            },
            False,
            None,
        ),
    )

    exit_code, message = manager_cmd.start_command(context_path=context_root)

    assert exit_code == 0
    assert message == "Manager 1761000000000000001 already running"


def test_start_command_replace_supersedes_before_start(tmp_path, monkeypatch):
    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    calls: list[str] = []

    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)

    def fake_replace(context_arg):
        assert context_arg is context
        calls.append("replace")
        return True, None

    def fake_start(context_arg):
        assert context_arg is context
        calls.append("start")
        return (
            {
                "tid": "1761000000000000002",
                "runtime_handle": _host_runtime_handle(12345),
            },
            True,
            None,
        )

    monkeypatch.setattr(core_manager_runtime, "replace_active_manager", fake_replace)
    monkeypatch.setattr(core_manager_runtime, "start_manager", fake_start)

    exit_code, message = manager_cmd.start_command(
        context_path=context_root,
        replace=True,
    )

    assert exit_code == 0
    assert message == "Started manager 1761000000000000002"
    assert calls == ["replace", "start"]


def test_start_command_replace_failure_does_not_start(tmp_path, monkeypatch):
    context_root = prepare_project_root(tmp_path / "proj")
    context = build_context(context_root)
    calls: list[str] = []

    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)
    monkeypatch.setattr(
        core_manager_runtime,
        "replace_active_manager",
        lambda context_arg: (False, "failed to send STOP"),
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "start_manager",
        lambda *args, **kwargs: calls.append("start"),
    )

    exit_code, message = manager_cmd.start_command(
        context_path=context_root,
        replace=True,
    )

    assert exit_code == 1
    assert message == "failed to send STOP"
    assert calls == []


def test_replace_active_manager_sends_stop_and_marks_superseded(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000010"

    registry_queue = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                context,
                tid,
                runtime_handle=_host_runtime_handle(os.getpid()),
                ctrl_in=f"manager.{tid}.ctrl_in",
            )
        )
    )
    registry_queue.close()
    monkeypatch.setattr(
        core_manager_runtime,
        "_await_manager_stop_confirmation",
        lambda *args, **kwargs: pytest.fail("replacement should not wait"),
    )

    replaced, message = core_manager_runtime.replace_active_manager(
        context,
        timeout=MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
    )

    assert replaced is True, message
    assert message is None
    ctrl_queue = Queue(
        f"manager.{tid}.ctrl_in",
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    try:
        assert ctrl_queue.read_one() == encode_control_message("STOP")
    finally:
        ctrl_queue.close()
    latest = _latest_manager_record(context, tid)
    assert latest is not None
    assert latest["status"] == SERVICE_STATUS_SUPERSEDED
    assert core_manager_runtime.select_active_manager(context) is None


def test_replace_active_manager_reselects_after_superseding_lower_tid(
    tmp_path,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    lower_tid = "1761000000000000011"
    higher_tid = "1761000000000000012"

    registry_queue = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    try:
        registry_queue.write(
            json.dumps(
                _manager_service_payload(
                    context,
                    lower_tid,
                    runtime_handle=_host_runtime_handle(os.getpid()),
                )
            )
        )
        registry_queue.write(
            json.dumps(
                _manager_service_payload(
                    context,
                    higher_tid,
                    runtime_handle=_host_runtime_handle(os.getpid()),
                )
            )
        )
    finally:
        registry_queue.close()

    replaced, message = core_manager_runtime.replace_active_manager(
        context,
        timeout=MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
    )

    assert replaced is True, message
    assert message is None
    lower_latest = _latest_manager_record(context, lower_tid)
    higher_latest = _latest_manager_record(context, higher_tid)
    assert lower_latest is not None
    assert higher_latest is not None
    assert lower_latest["status"] == SERVICE_STATUS_SUPERSEDED
    assert higher_latest["status"] == SERVICE_STATUS_SUPERSEDED


def test_stop_command_delegates_to_shared_lifecycle_helper(tmp_path, monkeypatch):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    calls: list[tuple[object, object, object, object, object]] = []

    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)

    def fake_stop_manager(
        context_arg,
        record,
        process=None,
        *,
        tid=None,
        timeout=MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
        force=False,
        stop_if_absent=False,
    ):
        calls.append((context_arg, record, tid, timeout, force))
        assert stop_if_absent is False
        return True, None

    monkeypatch.setattr(core_manager_runtime, "stop_manager", fake_stop_manager)

    exit_code, message = manager_cmd.stop_command(
        tid="1761000000000000001",
        force=False,
        timeout=0.1,
        context_path=context_root,
    )

    assert exit_code == 0
    assert message is None
    assert calls == [(context, None, "1761000000000000001", 0.1, False)]


def test_stop_command_without_tid_stops_active_manager(tmp_path, monkeypatch):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    active_record = {
        "tid": "1761000000000000006",
        "runtime_handle": _host_runtime_handle(os.getpid()),
    }
    select_calls: list[tuple[object, bool, object]] = []
    stop_calls: list[tuple[object, object, object, object, object]] = []

    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)

    def fake_select_active_manager(
        context_arg,
        *,
        probe_stale=False,
        probe_cache=None,
    ):
        select_calls.append((context_arg, probe_stale, probe_cache))
        return active_record

    def fake_stop_manager(
        context_arg,
        record,
        process=None,
        *,
        tid=None,
        timeout=MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
        force=False,
        stop_if_absent=False,
    ):
        del process, stop_if_absent
        stop_calls.append((context_arg, record, tid, timeout, force))
        return True, None

    monkeypatch.setattr(
        core_manager_runtime,
        "select_active_manager",
        fake_select_active_manager,
    )
    monkeypatch.setattr(core_manager_runtime, "stop_manager", fake_stop_manager)

    exit_code, message = manager_cmd.stop_command(
        tid=None,
        force=False,
        timeout=0.1,
        context_path=context_root,
    )

    assert exit_code == 0
    assert message is None
    assert select_calls == [(context, True, {})]
    assert stop_calls == [(context, active_record, "1761000000000000006", 0.1, False)]


def test_stop_command_without_tid_noops_when_no_active_manager(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    stop_calls: list[object] = []

    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)
    monkeypatch.setattr(
        core_manager_runtime,
        "select_active_manager",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "stop_manager",
        lambda *args, **kwargs: stop_calls.append(args),
    )

    exit_code, message = manager_cmd.stop_command(
        tid=None,
        force=False,
        timeout=0.1,
        context_path=context_root,
    )

    assert exit_code == 0
    assert message is None
    assert stop_calls == []


def test_stop_command_default_timeout_exceeds_manager_drain_budget(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    calls: list[float] = []

    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)

    def fake_stop_manager(
        context_arg,
        record,
        process=None,
        *,
        tid=None,
        timeout=MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
        force=False,
        stop_if_absent=False,
    ):
        del context_arg, record, process, tid, force, stop_if_absent
        calls.append(timeout)
        return True, None

    monkeypatch.setattr(core_manager_runtime, "stop_manager", fake_stop_manager)

    exit_code, message = manager_cmd.stop_command(
        tid="1761000000000000001",
        force=False,
        context_path=context_root,
    )

    assert exit_code == 0
    assert message is None
    assert calls == [MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS]
    assert (
        MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS
        >= MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS + 40.0
    )


def test_stop_manager_default_timeout_exceeds_manager_drain_budget(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    calls: list[float] = []

    def fake_stop_manager(
        context_arg,
        record,
        process=None,
        *,
        tid=None,
        timeout=MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS,
        force=False,
        stop_if_absent=False,
    ):
        del context_arg, record, process, tid, force, stop_if_absent
        calls.append(timeout)
        return True, None

    monkeypatch.setattr(core_manager_runtime, "stop_manager", fake_stop_manager)

    manager_cmd.stop_manager(context, "1761000000000000001")

    assert calls == [MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS]
    assert (
        MANAGER_STOP_CONFIRMATION_TIMEOUT_SECONDS
        >= MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS + 40.0
    )


def test_stop_command_rewrites_timeout_message(tmp_path, monkeypatch):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)

    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)
    monkeypatch.setattr(
        core_manager_runtime,
        "stop_manager",
        lambda *args, **kwargs: (
            False,
            "Manager 1761000000000000001 did not stop within 0.1s",
        ),
    )

    exit_code, message = manager_cmd.stop_command(
        tid="1761000000000000001",
        force=False,
        timeout=0.1,
        context_path=context_root,
    )

    assert exit_code == 1
    assert message == "Manager 1761000000000000001 did not stop within 0.1s"


def test_stop_command_writes_stop_for_active_manager(tmp_path):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000001"

    registry_queue = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                context,
                tid,
                runtime_handle=_host_runtime_handle(os.getpid()),
            )
        )
    )

    exit_code, message = manager_cmd.stop_command(
        tid=tid,
        force=False,
        timeout=0.1,
        context_path=context_root,
    )

    assert exit_code == 1
    assert message is not None
    assert "did not stop" in message
    ctrl_queue = Queue(
        f"T{tid}.ctrl_in",
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    assert ctrl_queue.read_one() == encode_control_message("STOP")


def test_stop_command_noops_for_stopped_manager(tmp_path):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000002"

    registry_queue = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    registry_queue.write(
        json.dumps(_manager_service_payload(context, tid, status="stopped"))
    )

    exit_code, message = manager_cmd.stop_command(
        tid=tid,
        force=False,
        timeout=0.1,
        context_path=context_root,
    )

    assert exit_code == 0
    assert message is None
    ctrl_queue = Queue(
        f"T{tid}.ctrl_in",
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    assert ctrl_queue.read_one() is None


def test_stop_command_uses_registry_control_queue(tmp_path):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000003"

    registry_queue = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                context,
                tid,
                runtime_handle=_host_runtime_handle(os.getpid()),
                ctrl_in=f"manager.{tid}.ctrl_in",
            )
        )
    )

    exit_code, message = manager_cmd.stop_command(
        tid=tid,
        force=False,
        timeout=0.1,
        context_path=context_root,
    )

    assert exit_code == 1
    assert message is not None
    assert "did not stop" in message
    ctrl_queue = Queue(
        f"manager.{tid}.ctrl_in",
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    assert ctrl_queue.read_one() == encode_control_message("STOP")


def test_stop_command_stop_if_absent_still_sends_stop(tmp_path):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000004"

    exit_code, message = manager_cmd.stop_command(
        tid=tid,
        force=False,
        timeout=0.1,
        context_path=context_root,
        stop_if_absent=True,
    )

    assert exit_code == 0
    assert message is None
    ctrl_queue = Queue(
        f"T{tid}.ctrl_in",
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    assert ctrl_queue.read_one() == encode_control_message("STOP")


def test_stop_command_waits_for_pid_exit_after_stopped_status(
    tmp_path, monkeypatch
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)

    monkeypatch.setattr(manager_cmd, "build_context", lambda spec_context=None: context)
    monkeypatch.setattr(
        core_manager_runtime,
        "stop_manager",
        lambda *args, **kwargs: (True, None),
    )

    exit_code, message = manager_cmd.stop_command(
        tid="1761000000000000005",
        force=False,
        timeout=1.0,
        context_path=context_root,
        stop_if_absent=True,
    )

    assert exit_code == 0
    assert message is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX only")
def test_list_command_omits_stale_active_manager(tmp_path) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000006"

    process = subprocess.Popen([sys.executable, "-c", "import os; os._exit(0)"])
    try:
        process.wait(timeout=2.0)
        registry_queue = Queue(
            WEFT_SERVICES_REGISTRY_QUEUE,
            db_path=context.broker_target,
            persistent=False,
            config=context.config,
        )
        registry_queue.write(
            json.dumps(
                {
                    "tid": tid,
                    "status": "active",
                    "name": "stale",
                    "pid": process.pid,
                    "role": "manager",
                    "requests": "custom.manager.requests",
                }
            )
        )
        exit_code, payload = manager_cmd.list_command(
            json_output=True, context_path=context_root
        )
    finally:
        process.wait()

    assert exit_code == 0
    assert tid not in {record["tid"] for record in json.loads(payload)}


def test_list_command_omits_stale_external_supervisor_manager(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000008"

    monkeypatch.setattr(
        "weft.core.manager_runtime.MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS",
        -1.0,
    )
    registry_queue = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    registry_queue.write(
        json.dumps(
            {
                "tid": tid,
                "status": "active",
                "name": "stale-supervised-manager",
                "runtime_handle": _external_supervisor_runtime_handle(),
                "role": "manager",
                "requests": "weft.spawn.requests",
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
                "outbox": "weft.manager.outbox",
            }
        )
    )

    exit_code, payload = manager_cmd.list_command(
        json_output=True, context_path=context_root
    )

    assert exit_code == 0
    assert tid not in {record["tid"] for record in json.loads(payload)}


@pytest.mark.skipif(os.name == "nt", reason="POSIX only")
def test_list_command_diagnostic_includes_stale_active_manager(tmp_path) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000018"

    process = subprocess.Popen([sys.executable, "-c", "import os; os._exit(0)"])
    try:
        process.wait(timeout=2.0)
        registry_queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
        try:
            registry_queue.write(
                json.dumps(
                    _manager_service_payload(
                        context,
                        tid,
                        name="stale-manager",
                        runtime_handle=_host_runtime_handle(process.pid),
                    )
                )
            )
        finally:
            registry_queue.close()
    finally:
        process.wait()

    exit_code, payload = manager_cmd.list_command(
        json_output=True,
        diagnostic=True,
        context_path=context_root,
    )

    assert exit_code == 0
    records = json.loads(payload)
    record = next(record for record in records if record["tid"] == tid)
    assert record["liveness"] == "stale"
    assert record["proof_source"] == "host-pid"
    assert record["canonical"] is False
    assert record["dispatch_eligible"] is False


def test_list_command_diagnostic_marks_lowest_live_manager_canonical(
    tmp_path,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    lower_tid = "1761000000000000019"
    higher_tid = "1761000000000000020"

    registry_queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    try:
        for tid in (higher_tid, lower_tid):
            registry_queue.write(
                json.dumps(
                    _manager_service_payload(
                        context,
                        tid,
                        runtime_handle=_host_runtime_handle(os.getpid()),
                    )
                )
            )
    finally:
        registry_queue.close()

    exit_code, payload = manager_cmd.list_command(
        json_output=True,
        diagnostic=True,
        context_path=context_root,
    )

    assert exit_code == 0
    records = {record["tid"]: record for record in json.loads(payload)}
    assert records[lower_tid]["liveness"] == "live"
    assert records[higher_tid]["liveness"] == "live"
    assert records[lower_tid]["proof_source"] == "host-pid"
    assert records[lower_tid]["canonical"] is True
    assert records[higher_tid]["canonical"] is False


def test_list_command_rescues_unreachable_host_pid_with_pong(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000022"

    registry_queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    try:
        registry_queue.write(
            json.dumps(
                _manager_service_payload(
                    context,
                    tid,
                    runtime_handle=_host_runtime_handle(987654321),
                )
            )
        )
    finally:
        registry_queue.close()
    monkeypatch.setattr(
        core_manager_runtime,
        "_manager_record_has_matched_pong",
        lambda *args, **kwargs: True,
    )

    exit_code, payload = manager_cmd.list_command(
        json_output=True,
        context_path=context_root,
    )

    assert exit_code == 0
    records = json.loads(payload)
    assert tid in {record["tid"] for record in records}


def test_ensure_manager_does_not_start_when_host_pid_incumbent_is_namespace_ambiguous(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000023"

    registry_queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    try:
        registry_queue.write(
            json.dumps(
                _manager_service_payload(
                    context,
                    tid,
                    runtime_handle=_host_runtime_handle(987654321),
                )
            )
        )
    finally:
        registry_queue.close()
    monkeypatch.setattr(
        core_manager_runtime,
        "detect_container_runtime",
        lambda: object(),
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_manager_record_has_matched_pong",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "start_manager",
        lambda *args, **kwargs: pytest.fail(
            "namespace-ambiguous incumbent must block manager startup"
        ),
    )

    record, started, process = core_manager_runtime.ensure_manager(context)

    assert record is not None
    assert record["tid"] == tid
    assert started is False
    assert process is None


def test_ensure_manager_starts_when_ambiguous_incumbent_strands_spawn_backlog(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    incumbent_tid = "1761000000000000024"
    replacement_tid = "1761000000000000025"

    registry_queue = context.queue(WEFT_SERVICES_REGISTRY_QUEUE, persistent=False)
    spawn_queue = context.queue(WEFT_SPAWN_REQUESTS_QUEUE, persistent=False)
    try:
        registry_queue.write(
            json.dumps(
                _manager_service_payload(
                    context,
                    incumbent_tid,
                    runtime_handle=_host_runtime_handle(987654321),
                )
            )
        )
        spawn_queue.write("pending-work")
    finally:
        registry_queue.close()
        spawn_queue.close()
    monkeypatch.setattr(
        core_manager_runtime,
        "MANAGER_NAMESPACE_AMBIGUOUS_BACKLOG_GRACE_SECONDS",
        0.0,
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "detect_container_runtime",
        lambda: object(),
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_manager_record_has_matched_pong",
        lambda *args, **kwargs: False,
    )

    def _start_replacement(*_args, **_kwargs):
        return {"tid": replacement_tid, "status": "active"}, True, None

    monkeypatch.setattr(core_manager_runtime, "start_manager", _start_replacement)

    record, started, process = core_manager_runtime.ensure_manager(context)

    assert record is not None
    assert record["tid"] == replacement_tid
    assert started is True
    assert process is None


def test_stop_command_force_reports_fresh_external_supervisor_without_host_pid(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000009"

    monkeypatch.setattr(
        "weft.core.manager_runtime.MANAGER_EXTERNAL_SUPERVISOR_STALE_AFTER_SECONDS",
        60.0,
    )
    registry_queue = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                context,
                tid,
                name="fresh-supervised-manager",
                runtime_handle=_external_supervisor_runtime_handle(),
            )
        )
    )

    exit_code, message = manager_cmd.stop_command(
        tid=tid,
        force=True,
        timeout=0.0,
        context_path=context_root,
    )

    assert exit_code == 1
    assert message is not None
    assert "externally supervised" in message
    assert "no host PID" in message


def test_stop_command_force_ignores_registry_only_pid_without_mapping(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000007"

    registry_queue = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    registry_queue.write(
        json.dumps(
            {
                "tid": tid,
                "status": "active",
                "name": "legacy-manager",
                "pid": os.getpid(),
                "role": "manager",
                "requests": "legacy.requests",
            }
        )
    )

    monkeypatch.setattr(
        "weft.core.manager_runtime.terminate_process_tree",
        lambda *args, **kwargs: pytest.fail(
            "force stop must not trust an uncorroborated registry pid"
        ),
    )

    exit_code, message = manager_cmd.stop_command(
        tid=tid,
        force=True,
        timeout=0.0,
        context_path=context_root,
        stop_if_absent=True,
    )

    assert exit_code == 0
    assert message is None


def test_stop_command_force_replaces_active_registry_record(
    tmp_path,
    monkeypatch,
) -> None:
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    tid = "1761000000000000010"
    kill_pid = 8765

    registry_queue = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    try:
        registry_queue.write(
            json.dumps(
                _manager_service_payload(
                    context,
                    tid,
                    runtime_handle=_host_runtime_handle(kill_pid),
                )
            )
        )
    finally:
        registry_queue.close()

    monkeypatch.setattr(
        "weft.core.manager_runtime._lookup_manager_pid",
        lambda *args, **kwargs: kill_pid,
    )
    termination_started = False
    lifecycle_events: list[tuple[str, int]] = []

    def is_pid_alive(pid: int | None) -> bool:
        lifecycle_events.append(
            ("alive_after_terminate" if termination_started else "alive", pid or 0)
        )
        return pid == kill_pid and not termination_started

    def terminate(pid: int, **_kwargs: object) -> set[int]:
        nonlocal termination_started
        lifecycle_events.append(("terminate", pid))
        termination_started = True
        return {pid}

    monkeypatch.setattr("weft.core.manager_runtime.pid_is_live", is_pid_alive)
    monkeypatch.setattr("weft.core.manager_runtime.terminate_process_tree", terminate)

    exit_code, message = manager_cmd.stop_command(
        tid=tid,
        force=True,
        timeout=0.0,
        context_path=context_root,
    )

    assert exit_code == 0
    assert message is None
    assert ("alive", kill_pid) in lifecycle_events
    assert lifecycle_events[-2:] == [
        ("terminate", kill_pid),
        ("alive_after_terminate", kill_pid),
    ]

    reader = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    try:
        records = [payload for payload, _timestamp in iter_queue_json_entries(reader)]
    finally:
        reader.close()

    assert len(records) == 1
    assert records[0]["owner_tid"] == tid
    assert records[0]["status"] == "stopped"


@pytest.mark.parametrize(
    ("error", "alive_after_error", "expected_success", "message_fragment"),
    [
        (ProcessLookupError("gone"), False, True, None),
        (ProcessLookupError("uncertain"), True, False, "Failed to terminate"),
        (OSError("gone"), False, True, None),
        (OSError("uncertain"), True, False, "Failed to terminate"),
        (PermissionError("denied"), True, False, "Permission denied"),
    ],
)
def test_stop_manager_force_requires_dead_pid_evidence_after_signal_failure(
    tmp_path,
    monkeypatch,
    error: OSError,
    alive_after_error: bool,
    expected_success: bool,
    message_fragment: str | None,
) -> None:
    context = build_context(prepare_project_root(tmp_path / "ctx"))
    tid = "1761000000000000010"
    kill_pid = 8765
    record = _manager_service_record(context, tid)
    record["runtime_handle"] = _host_runtime_handle(kill_pid)
    signal_attempted = False
    marked: list[str] = []

    monkeypatch.setattr(
        core_manager_runtime, "_send_stop", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_await_manager_stop_confirmation",
        lambda *_args, **_kwargs: (False, record),
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_lookup_manager_pid",
        lambda *_args, **_kwargs: kill_pid,
    )

    def is_pid_alive(pid: int | None) -> bool:
        assert pid == kill_pid
        return alive_after_error if signal_attempted else True

    def terminate(*_args: object, **_kwargs: object) -> set[int]:
        nonlocal signal_attempted
        signal_attempted = True
        raise error

    def mark_stopped(*_args: object, **_kwargs: object) -> bool:
        marked.append(tid)
        return True

    monkeypatch.setattr(core_manager_runtime, "pid_is_live", is_pid_alive)
    monkeypatch.setattr(core_manager_runtime, "terminate_process_tree", terminate)
    monkeypatch.setattr(core_manager_runtime, "_mark_manager_stopped", mark_stopped)

    success, message = core_manager_runtime.stop_manager(
        context,
        record,
        timeout=0.0,
        force=True,
    )

    assert success is expected_success
    if message_fragment is None:
        assert message is None
    else:
        assert message_fragment in (message or "")
    assert marked == ([tid] if expected_success else [])


@pytest.mark.parametrize(
    ("error", "exited_after_error", "expected_success", "message_fragment"),
    [
        (ProcessLookupError("gone"), True, True, None),
        (ProcessLookupError("uncertain"), False, False, "Failed to terminate"),
        (OSError("gone"), True, True, None),
        (OSError("uncertain"), False, False, "Failed to terminate"),
        (PermissionError("denied"), False, False, "Permission denied"),
    ],
)
def test_stop_manager_force_requires_process_exit_evidence_after_signal_failure(
    tmp_path,
    monkeypatch,
    error: OSError,
    exited_after_error: bool,
    expected_success: bool,
    message_fragment: str | None,
) -> None:
    context = build_context(prepare_project_root(tmp_path / "ctx"))
    tid = "1761000000000000010"
    record = _manager_service_record(context, tid)
    marked: list[str] = []

    class FailingProcess:
        signal_attempted = False

        def poll(self) -> int | None:
            if self.signal_attempted and exited_after_error:
                return 0
            return None

        def terminate(self) -> None:
            self.signal_attempted = True
            raise error

    process = FailingProcess()
    monkeypatch.setattr(
        core_manager_runtime, "_send_stop", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_await_manager_stop_confirmation",
        lambda *_args, **_kwargs: (False, record),
    )
    monkeypatch.setattr(
        core_manager_runtime,
        "_lookup_manager_pid",
        lambda *_args, **_kwargs: None,
    )

    def mark_stopped(*_args: object, **_kwargs: object) -> bool:
        marked.append(tid)
        return True

    monkeypatch.setattr(core_manager_runtime, "_mark_manager_stopped", mark_stopped)

    success, message = core_manager_runtime.stop_manager(
        context,
        record,
        process=process,  # type: ignore[arg-type]
        timeout=0.0,
        force=True,
    )

    assert success is expected_success
    if message_fragment is None:
        assert message is None
    else:
        assert message_fragment in (message or "")
    assert marked == ([tid] if expected_success else [])


def test_list_command_returns_table(tmp_path):
    context_root = prepare_project_root(tmp_path / "ctx")
    context = build_context(context_root)
    registry_queue = Queue(
        WEFT_SERVICES_REGISTRY_QUEUE,
        db_path=context.broker_target,
        persistent=False,
        config=context.config,
    )
    registry_queue.write(
        json.dumps(
            _manager_service_payload(
                context,
                "1",
                name="alpha",
                runtime_handle=_host_runtime_handle(os.getpid()),
            )
        )
    )

    exit_code, payload = manager_cmd.list_command(
        json_output=False, context_path=context_root
    )
    assert exit_code == 0
    assert "alpha" in payload


def test_status_command_not_found(tmp_path):
    context_root = prepare_project_root(tmp_path / "ctx")
    build_context(context_root)
    exit_code, payload = manager_cmd.status_command(
        tid="999", json_output=False, context_path=context_root
    )
    assert exit_code == 1
    assert "not found" in payload.lower()
