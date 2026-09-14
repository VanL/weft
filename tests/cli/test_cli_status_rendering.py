"""Live root status rendering contracts [CLI-1.2.1], [SB-0.2]."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.commands.test_status import _FakeQueueChangeMonitor
from tests.helpers.test_backend import prepare_project_root
from weft.cli.app import _render_status_snapshot, _status_json_payload, app
from weft.commands import system as status_cmd
from weft.commands import types
from weft.context import build_context

pytestmark = pytest.mark.shared


def test_cmd_status_text_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = prepare_project_root(tmp_path)
    ctx = build_context(spec_context=root)
    queue = ctx.queue("status.queue", persistent=True)
    queue.write("payload")

    _render_status_snapshot(status_cmd.cmd_status(context=root), json_output=False)
    payload = capsys.readouterr().out

    assert payload is not None
    lines = payload.splitlines()
    assert lines[0].startswith("total_messages: ")

    ts_line = next(line for line in lines if line.startswith("last_timestamp: "))
    assert ts_line.removeprefix("last_timestamp: ").isdigit()

    size_line = next(line for line in lines if line.startswith("db_size: "))
    assert "bytes" in size_line
    assert "Services:" in lines


def test_status_json_projects_only_owned_broker_identity_fields() -> None:
    first_id = 1_779_200_000_000_000_001
    second_id = 1_779_200_000_000_000_002
    wall_clock_ns = 1_779_200_000_000_000_099
    broker = status_cmd.BrokerStatusSnapshot(
        total_messages=7,
        last_timestamp=first_id,
        db_size=4096,
    )
    managers = [
        types.ManagerSnapshot(
            tid="1779200000000000100",
            name="manager",
            status="active",
            runtime_handle=None,
            timestamp=first_id,
        )
    ]
    services = [
        status_cmd.ServiceSnapshot(
            key="service",
            name="service",
            desired=True,
            enabled=True,
            status="running",
            evidence="registry",
            updated_at=second_id,
            pid=4321,
        )
    ]
    tasks = [
        status_cmd.TaskSnapshot(
            tid="1779200000000000200",
            tid_short="000200",
            name="broker-backed",
            status="completed",
            event="work_completed",
            activity=None,
            waiting_on=None,
            started_at=wall_clock_ns,
            completed_at=wall_clock_ns,
            last_timestamp=first_id,
            duration_seconds=1.0,
            runner="host",
            runtime_handle=None,
            runtime=None,
            metadata={},
            reconciliation={
                "classification": "terminal_ctrl_out",
                "observed_at": second_id,
            },
        ),
        status_cmd.TaskSnapshot(
            tid="1779200000000000201",
            tid_short="000201",
            name="wall-clock",
            status="failed",
            event="unknown",
            activity=None,
            waiting_on=None,
            started_at=None,
            completed_at=None,
            last_timestamp=wall_clock_ns,
            duration_seconds=None,
            runner="host",
            runtime_handle=None,
            runtime=None,
            metadata={},
            reconciliation={
                "classification": "claimed_result_without_terminal",
                "observed_at": wall_clock_ns,
            },
        ),
        status_cmd.TaskSnapshot(
            tid="1779200000000000202",
            tid_short="000202",
            name="monitor-backed",
            status="completed",
            event="work_completed",
            activity=None,
            waiting_on=None,
            started_at=wall_clock_ns,
            completed_at=wall_clock_ns,
            last_timestamp=second_id,
            duration_seconds=1.0,
            runner="host",
            runtime_handle=None,
            runtime=None,
            metadata={},
            reconciliation={
                "classification": "terminal_monitor_store",
                "reason": "raw_task_log_retired",
            },
        ),
        status_cmd.TaskSnapshot(
            tid="1779200000000000203",
            tid_short="000203",
            name="stale-task-log",
            status="running",
            event="task_started",
            activity=None,
            waiting_on=None,
            started_at=wall_clock_ns,
            completed_at=None,
            last_timestamp=first_id,
            duration_seconds=1.0,
            runner="host",
            runtime_handle=None,
            runtime=None,
            metadata={},
            reconciliation={
                "classification": "stale_liveness",
                "observed_at": wall_clock_ns,
            },
        ),
        status_cmd.TaskSnapshot(
            tid="1779200000000000204",
            tid_short="000204",
            name="pipeline-clock",
            status="running",
            event="pipeline_status",
            activity=None,
            waiting_on=None,
            started_at=wall_clock_ns,
            completed_at=None,
            last_timestamp=wall_clock_ns,
            duration_seconds=1.0,
            runner="host",
            runtime_handle=None,
            runtime=None,
            metadata={},
            pipeline_status={"timestamp": wall_clock_ns},
        ),
        status_cmd.TaskSnapshot(
            tid="1779200000000000205",
            tid_short="000205",
            name="terminal-control-clock",
            status="completed",
            event="ctrl_out_terminal",
            activity=None,
            waiting_on=None,
            started_at=wall_clock_ns,
            completed_at=wall_clock_ns,
            last_timestamp=wall_clock_ns,
            duration_seconds=1.0,
            runner="host",
            runtime_handle=None,
            runtime=None,
            metadata={},
        ),
    ]

    snapshot = types.SystemStatusSnapshot(
        broker=broker.to_dict(),
        managers=managers,
        services=services,
        tasks=[status_cmd._public_task_snapshot(task) for task in tasks],
    )
    payload = _status_json_payload(snapshot)
    assert set(payload) == set(asdict(snapshot))
    for field in ("managers", "services", "tasks"):
        assert [set(row) for row in payload[field]] == [
            set(row) for row in asdict(snapshot)[field]
        ]

    assert payload["broker"] == {
        "total_messages": 7,
        "last_timestamp": "1779200000000000001",
        "db_size": 4096,
    }
    assert payload["managers"][0]["timestamp"] == "1779200000000000001"
    assert payload["services"][0]["updated_at"] == "1779200000000000002"
    assert payload["services"][0]["pid"] == 4321
    assert payload["tasks"][0]["last_timestamp"] == "1779200000000000001"
    assert payload["tasks"][0]["reconciliation"]["observed_at"] == (
        "1779200000000000002"
    )
    assert payload["tasks"][0]["started_at"] == wall_clock_ns
    assert payload["tasks"][1]["last_timestamp"] == wall_clock_ns
    assert payload["tasks"][1]["reconciliation"]["observed_at"] == wall_clock_ns
    assert payload["tasks"][2]["last_timestamp"] == "1779200000000000002"
    assert payload["tasks"][3]["last_timestamp"] == "1779200000000000001"
    assert payload["tasks"][3]["reconciliation"]["observed_at"] == wall_clock_ns
    assert payload["tasks"][4]["last_timestamp"] == wall_clock_ns
    assert payload["tasks"][5]["last_timestamp"] == wall_clock_ns


def test_watch_task_events_json_formats_broker_message_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    root = prepare_project_root(tmp_path)
    tid = "1844674407370955166"
    message_id = 1_779_500_000_000_000_001
    log_iterations = iter(
        [
            [
                (
                    {
                        "tid": tid,
                        "status": "completed",
                        "event": "work_completed",
                        "taskspec": {"name": "status-task"},
                    },
                    message_id,
                )
            ],
            [],
        ]
    )

    monkeypatch.setattr(status_cmd, "QueueChangeMonitor", _FakeQueueChangeMonitor)
    monkeypatch.setattr(
        status_cmd,
        "_iter_log_events",
        lambda *_args, **_kwargs: next(log_iterations, []),
    )

    result = CliRunner().invoke(
        app, ["status", "--watch", "--json", "--context", str(root)]
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["timestamp"] == "1779500000000000001"
    assert set(payload) == {"tid", "event_type", "timestamp", "payload"}


def test_status_service_health_warnings_are_rendered(
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = types.ServiceSnapshot(
        key="task_monitor",
        name="monitor",
        desired=True,
        enabled=True,
        status="running",
        evidence="registry",
        diagnostics={
            "task_monitor": {
                "task_log_external": {"healthy": False, "deferred_pending": 3}
            }
        },
    )
    _render_status_snapshot(
        types.SystemStatusSnapshot(
            broker={}, managers=[], tasks=[], services=[service]
        ),
        json_output=False,
    )
    output = capsys.readouterr().out
    assert "warning=external-log-unhealthy" in output
    assert "warning=deferred-writes-pending" in output
    assert "deferred_writes=3" in output
