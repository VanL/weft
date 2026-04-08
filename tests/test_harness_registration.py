from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from simplebroker import Queue
from tests.conftest import _register_from_json
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_GLOBAL_LOG_QUEUE, WEFT_TID_MAPPINGS_QUEUE
from weft.commands import tasks as task_cmd
from weft.commands import worker as worker_cmd


@pytest.mark.sqlite_only
def test_register_from_json_routes_manager_tids_to_worker_tracking() -> None:
    harness = WeftTestHarness()
    try:
        _register_from_json(
            harness,
            {
                "tid": "1775630560447778816",
                "role": "manager",
                "pid": 424242,
            },
        )
        _register_from_json(
            harness,
            {
                "tid": "1775630560447778816",
                "metadata": {"role": "manager"},
            },
        )
        _register_from_json(
            harness,
            {
                "tid": "1775630560739303424",
                "status": "completed",
            },
        )

        assert harness.registered_worker_tids() == {"1775630560447778816"}
        assert harness.registered_tids() == {"1775630560739303424"}
    finally:
        harness.cleanup()


@pytest.mark.sqlite_only
def test_harness_force_termination_targets_managed_pids_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = WeftTestHarness()
    terminated: list[int] = []
    try:
        harness._context = SimpleNamespace(backend_name="postgres")
        harness.register_pid(101, kind="owner")
        harness.register_pid(202, kind="managed")
        harness.register_pid(303, kind="managed")

        monkeypatch.setattr(harness, "_should_skip_pid", lambda pid: False)
        monkeypatch.setattr(
            harness, "_terminate_pid", lambda pid: terminated.append(pid)
        )

        harness._terminate_registered_pids()

        assert terminated == [202, 303]
    finally:
        harness._closed = True
        harness._tempdir.cleanup()


@pytest.mark.sqlite_only
def test_harness_cleanup_preserve_database_avoids_force_termination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = WeftTestHarness()
    repo_cwd = os.getcwd()
    try:
        harness.__enter__()
        harness.register_pid(202, kind="managed")
        monkeypatch.setattr(harness, "_collect_pid_mappings", lambda: None)
        monkeypatch.setattr(
            harness,
            "_cleanup_worker_records",
            lambda: {},
        )
        monkeypatch.setattr(
            harness,
            "_live_task_tids_from_mappings",
            lambda: [],
        )
        monkeypatch.setattr(
            harness,
            "_live_registered_pids",
            lambda: [202],
        )
        terminated: list[int] = []
        monkeypatch.setattr(
            harness, "_terminate_pid", lambda pid: terminated.append(pid)
        )

        with pytest.raises(RuntimeError, match="preserve_database=True"):
            harness.cleanup(preserve_database=True)

        assert terminated == []
    finally:
        os.chdir(repo_cwd)
        harness._closed = True
        harness._tempdir.cleanup()


@pytest.mark.sqlite_only
def test_harness_stop_active_workers_stops_registered_task_and_worker_tids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = WeftTestHarness()
    try:
        harness.__enter__()
        harness.register_tid("1775630560739303424")
        harness.register_worker_tid("1775630560447778816")
        monkeypatch.setattr(
            harness,
            "_list_active_worker_records",
            lambda: [{"tid": "1775630560999999999", "status": "active"}],
        )
        monkeypatch.setattr(harness, "_collect_pid_mappings", lambda: None)
        monkeypatch.setattr(
            harness,
            "_wait_for_registered_pids_to_exit",
            lambda **kwargs: [],
        )
        monkeypatch.setattr(harness, "_drain_registry_queue", lambda: None)

        worker_calls: list[str] = []
        task_calls: list[str] = []

        monkeypatch.setattr(
            harness,
            "_send_worker_stop",
            lambda tid, *, record: worker_calls.append(tid),
        )
        monkeypatch.setattr(
            harness,
            "_send_task_stop",
            lambda tid: task_calls.append(tid),
        )
        monkeypatch.setattr(
            task_cmd, "kill_tasks", lambda tids, **kwargs: len(tuple(tids))
        )
        monkeypatch.setattr(
            worker_cmd,
            "stop_command",
            lambda **kwargs: (0, None),
        )

        harness._stop_active_workers()

        assert worker_calls == ["1775630560447778816", "1775630560999999999"]
        assert task_calls == ["1775630560739303424"]
    finally:
        harness.cleanup()


@pytest.mark.sqlite_only
def test_harness_stop_active_workers_does_not_fan_out_worker_tid_as_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = WeftTestHarness()
    repo_cwd = os.getcwd()
    try:
        harness.__enter__()
        monkeypatch.setattr(
            harness,
            "_list_active_worker_records",
            lambda: [{"tid": "1775630560447778816", "status": "active"}],
        )
        harness._load_tid_mapping_payloads = lambda: [  # type: ignore[method-assign]
            {
                "full": "1775630560447778816",
                "pid": 424242,
                "task_pid": 424242,
                "managed_pids": [],
            }
        ]
        monkeypatch.setattr(
            harness,
            "_wait_for_registered_pids_to_exit",
            lambda **kwargs: [],
        )
        monkeypatch.setattr(harness, "_drain_registry_queue", lambda: None)

        worker_calls: list[str] = []
        task_calls: list[str] = []
        kill_calls: list[tuple[str, ...]] = []

        monkeypatch.setattr(
            harness,
            "_send_worker_stop",
            lambda tid, *, record: worker_calls.append(tid),
        )
        monkeypatch.setattr(
            harness,
            "_send_task_stop",
            lambda tid: task_calls.append(tid),
        )
        monkeypatch.setattr(
            task_cmd,
            "kill_tasks",
            lambda tids, **kwargs: kill_calls.append(tuple(tids)),
        )
        monkeypatch.setattr(
            worker_cmd,
            "stop_command",
            lambda **kwargs: (0, None),
        )

        harness._stop_active_workers()

        assert worker_calls == ["1775630560447778816"]
        assert task_calls == []
        assert kill_calls == []
    finally:
        os.chdir(repo_cwd)
        harness._closed = True
        harness._tempdir.cleanup()


@pytest.mark.sqlite_only
def test_harness_stop_active_workers_does_not_fan_out_in_process_task_tid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = WeftTestHarness()
    repo_cwd = os.getcwd()
    try:
        harness.__enter__()
        monkeypatch.setattr(harness, "_list_active_worker_records", lambda: [])
        harness._load_tid_mapping_payloads = lambda: [  # type: ignore[method-assign]
            {
                "full": "1775630561555555555",
                "pid": harness._self_pid,
                "task_pid": harness._self_pid,
                "managed_pids": [],
            }
        ]
        monkeypatch.setattr(
            harness,
            "_wait_for_registered_pids_to_exit",
            lambda **kwargs: [],
        )
        monkeypatch.setattr(harness, "_drain_registry_queue", lambda: None)

        task_calls: list[str] = []
        kill_calls: list[tuple[str, ...]] = []

        monkeypatch.setattr(
            harness,
            "_send_task_stop",
            lambda tid: task_calls.append(tid),
        )
        monkeypatch.setattr(
            task_cmd,
            "kill_tasks",
            lambda tids, **kwargs: kill_calls.append(tuple(tids)),
        )
        monkeypatch.setattr(
            worker_cmd,
            "stop_command",
            lambda **kwargs: (0, None),
        )

        harness._stop_active_workers()

        assert task_calls == []
        assert kill_calls == []
    finally:
        os.chdir(repo_cwd)
        harness._closed = True
        harness._tempdir.cleanup()


@pytest.mark.sqlite_only
def test_harness_cleanup_preserve_database_skips_registry_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = WeftTestHarness()
    repo_cwd = os.getcwd()
    try:
        harness.__enter__()
        preserve_calls: list[str] = []
        drain_calls: list[str] = []
        terminate_calls: list[str] = []
        monkeypatch.setattr(
            harness,
            "_cleanup_preserving_database",
            lambda: preserve_calls.append("preserve"),
        )
        monkeypatch.setattr(
            harness,
            "_terminate_registered_pids",
            lambda: terminate_calls.append("terminated"),
        )
        monkeypatch.setattr(harness, "_remove_database_files", lambda: None)
        monkeypatch.setattr(
            harness,
            "_drain_registry_queue",
            lambda: drain_calls.append("drained"),
        )

        harness.cleanup(preserve_database=True)

        assert preserve_calls == ["preserve"]
        assert drain_calls == []
        assert terminate_calls == []
    finally:
        os.chdir(repo_cwd)
        harness._closed = True
        harness._tempdir.cleanup()


@pytest.mark.sqlite_only
def test_collect_pid_mappings_registers_discovered_task_tids() -> None:
    harness = WeftTestHarness()
    try:
        harness._registered_worker_tids.add("1775630560447778816")
        monkeypatch_payloads = [
            {
                "full": "1775630560739303424",
                "pid": 424242,
                "managed_pids": [424243],
            },
            {
                "full": "1775630560447778816",
                "pid": 424244,
            },
            {
                "full": "1775630560999999999",
                "pid": 424245,
                "role": "manager",
            },
        ]
        harness._load_tid_mapping_payloads = lambda: monkeypatch_payloads  # type: ignore[method-assign]

        harness._collect_pid_mappings()

        assert harness.registered_tids() == {"1775630560739303424"}
        assert harness.registered_worker_tids() == {
            "1775630560447778816",
            "1775630560999999999",
        }
        assert 424242 in harness._registered_pids
        assert 424243 in harness._registered_pids
    finally:
        harness._closed = True
        harness._tempdir.cleanup()


@pytest.mark.sqlite_only
def test_live_task_tids_ignore_manager_role_mappings() -> None:
    harness = WeftTestHarness()
    try:
        harness._load_tid_mapping_payloads = lambda: [  # type: ignore[method-assign]
            {
                "full": "1775630560739303424",
                "task_pid": 424242,
                "pid": 424242,
                "managed_pids": [],
            },
            {
                "full": "1775630560999999999",
                "task_pid": 424245,
                "pid": 424245,
                "managed_pids": [],
                "role": "manager",
            },
        ]
        harness._pid_alive = lambda pid: pid == 424242 or pid == 424245  # type: ignore[method-assign]
        harness._should_skip_pid = lambda pid: False  # type: ignore[method-assign]

        assert harness._live_task_tids_from_mappings() == ["1775630560739303424"]
    finally:
        harness._closed = True
        harness._tempdir.cleanup()


@pytest.mark.sqlite_only
def test_live_task_tids_ignore_terminal_log_events() -> None:
    harness = WeftTestHarness()
    try:
        harness._load_tid_mapping_payloads = lambda: [  # type: ignore[method-assign]
            {
                "full": "1775630560739303424",
                "task_pid": 424242,
                "pid": 424242,
                "managed_pids": [],
            }
        ]
        harness._latest_task_events = lambda: {  # type: ignore[method-assign]
            "1775630560739303424": "work_completed"
        }
        harness._pid_alive = lambda pid: pid == 424242  # type: ignore[method-assign]
        harness._should_skip_pid = lambda pid: False  # type: ignore[method-assign]

        assert harness._live_task_tids_from_mappings() == []
    finally:
        harness._closed = True
        harness._tempdir.cleanup()


@pytest.mark.sqlite_only
def test_wait_for_completion_treats_control_stop_as_terminal_event() -> None:
    harness = WeftTestHarness()
    repo_cwd = os.getcwd()
    try:
        harness.__enter__()
        tid = "1775630560739303424"
        log_queue = Queue(
            WEFT_GLOBAL_LOG_QUEUE,
            db_path=harness.context.broker_target,
            persistent=False,
            config=harness.context.broker_config,
        )
        try:
            log_queue.write(json.dumps({"tid": tid, "event": "control_stop"}))
        finally:
            log_queue.close()

        with pytest.raises(RuntimeError, match=rf"Task {tid} reported control_stop"):
            harness.wait_for_completion(tid, timeout=0.2)
    finally:
        os.chdir(repo_cwd)
        harness.cleanup()


@pytest.mark.sqlite_only
def test_wait_for_completion_timeout_includes_tid_debug_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = WeftTestHarness()
    repo_cwd = os.getcwd()
    try:
        harness.__enter__()
        tid = "1775630560739303424"
        mapping_queue = Queue(
            WEFT_TID_MAPPINGS_QUEUE,
            db_path=harness.context.broker_target,
            persistent=False,
            config=harness.context.broker_config,
        )
        try:
            mapping_queue.write(
                json.dumps(
                    {
                        "full": tid,
                        "pid": 424242,
                        "task_pid": 424242,
                        "managed_pids": [434343],
                    }
                )
            )
        finally:
            mapping_queue.close()

        monkeypatch.setattr(harness, "_pid_alive", lambda pid: pid == 424242)
        monkeypatch.setattr(harness, "_should_skip_pid", lambda pid: False)

        with pytest.raises(
            TimeoutError, match=rf"Timed out waiting for task {tid}"
        ) as exc_info:
            harness.wait_for_completion(tid, timeout=0.01)

        message = str(exc_info.value)
        assert "Task completion timeout snapshot:" in message
        assert f"  tid={tid}" in message
        assert "  latest_tid_mapping=" in message
        assert '"managed_pids": [434343]' in message
        assert "  outbox_present=False" in message
        assert "  live_candidate_pids=[424242]" in message
        assert "WeftTestHarness snapshot:" in message
    finally:
        os.chdir(repo_cwd)
        harness.cleanup()


@pytest.mark.sqlite_only
def test_cleanup_preserving_database_stops_workers_without_task_fanout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = WeftTestHarness()
    repo_cwd = os.getcwd()
    try:
        harness.__enter__()
        harness.register_worker_tid("1775630560447778816")
        monkeypatch.setattr(harness, "_collect_pid_mappings", lambda: None)
        monkeypatch.setattr(
            harness,
            "_cleanup_worker_records",
            lambda: {"1775630560447778816": {"tid": "1775630560447778816"}},
        )
        monkeypatch.setattr(
            harness,
            "_live_task_tids_from_mappings",
            lambda: [],
        )
        monkeypatch.setattr(harness, "_live_registered_pids", lambda: [])

        worker_calls: list[str] = []
        monkeypatch.setattr(
            harness,
            "_send_worker_stop",
            lambda tid, *, record: worker_calls.append(tid),
        )
        monkeypatch.setattr(
            harness,
            "_send_task_stop",
            lambda tid: pytest.fail(f"unexpected task stop for {tid}"),
        )

        harness._cleanup_preserving_database()

        assert worker_calls == ["1775630560447778816"]
    finally:
        os.chdir(repo_cwd)
        harness._closed = True
        harness._tempdir.cleanup()
