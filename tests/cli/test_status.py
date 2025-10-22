"""CLI status command tests."""

from __future__ import annotations

import json

from simplebroker import Queue

from tests.conftest import run_cli
from weft._constants import WEFT_SPAWN_REQUESTS_QUEUE, WEFT_WORKERS_REGISTRY_QUEUE
from weft.context import build_context


def test_status_reports_no_managers(workdir) -> None:
    rc, out, err = run_cli("status", cwd=workdir)

    assert rc == 0
    assert "Managers: none registered" in out
    assert err == ""


def test_status_json_includes_manager_records(workdir) -> None:
    context = build_context(spec_context=workdir)
    registry = Queue(
        WEFT_WORKERS_REGISTRY_QUEUE,
        db_path=str(context.database_path),
        persistent=False,
        config=context.broker_config,
    )

    record = {
        "tid": "1762000000000000999",
        "name": "cli-manager",
        "status": "active",
        "pid": 12345,
        "role": "manager",
        "requests": WEFT_SPAWN_REQUESTS_QUEUE,
        "timestamp": 1762000000000001999,
    }

    registry.write(json.dumps(record))

    try:
        rc, out, err = run_cli("status", "--json", cwd=workdir)

        assert rc == 0
        assert err == ""

        payload = json.loads(out)
        managers = payload["managers"]
        assert any(item.get("tid") == record["tid"] for item in managers)
        entry = next(item for item in managers if item.get("tid") == record["tid"])
        assert entry["requests"] == WEFT_SPAWN_REQUESTS_QUEUE
        assert entry["pid"] == record["pid"]
    finally:
        registry.read_many(limit=100)
