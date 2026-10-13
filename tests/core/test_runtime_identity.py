"""Runtime identity transport and owner attribution contracts [MF-1], [MF-5]."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import llm
import pytest
from llm.models import Model, Prompt, Response

from simplebroker import BrokerTarget
from tests.helpers.typing import BrokerEnv
from tests.helpers.weft_harness import WeftTestHarness
from tests.tasks.test_task_execution import make_function_taskspec
from weft.context import build_context, service_context_key
from weft.core import runtime_identity as identity_module
from weft.core.launcher import launch_task_process
from weft.core.runners.host import HostTaskRunner
from weft.core.runtime_identity import (
    AdmissionRecord,
    RuntimeIdentity,
    canonical_runtime_context,
    connection_owner_label,
    install_runtime_identity,
)
from weft.core.task_state import task_state_queue_name
from weft.core.tasks import Consumer

pytestmark = [pytest.mark.shared, pytest.mark.timeout(60)]


def observe_runtime_identity(_payload: Any) -> str:
    """Expose the actual spawned application process identity through its result."""
    if importlib.util.find_spec("weft.core.runtime_identity") is None:
        return json.dumps({"tid": None})
    identity_module = importlib.import_module("weft.core.runtime_identity")
    identity = identity_module.get_runtime_identity()
    if identity is None:
        return json.dumps({"tid": None})
    return json.dumps({"tid": identity.tid, "context": identity.context})


def test_spawned_function_receives_task_identity(
    broker_env: BrokerEnv,
    weft_harness: WeftTestHarness,
    workdir: Path,
) -> None:
    """The wrapper must transport its identity before application code runs.

    A same-context client in the worker otherwise enters the root FIFO. Existing
    runner tests prove result delivery, but cannot detect missing task ancestry.
    """
    target, make_queue = broker_env
    unique_tid = str(time.time_ns())
    spec = make_function_taskspec(
        unique_tid,
        "tests.core.test_runtime_identity:observe_runtime_identity",
        weft_context=str(workdir),
    )
    make_queue(spec.io.inputs["inbox"]).write("payload")
    process = launch_task_process(
        Consumer, target, spec, config=weft_harness.context.config
    )
    assert process.pid is not None
    weft_harness.register_pid(process.pid, unique_tid)
    try:
        process.join(30.0)
        assert not process.is_alive()
        assert process.exitcode == 0
        result = make_queue(spec.io.outputs["outbox"]).read_one()
        assert result is not None
        assert json.loads(result) == {
            "tid": unique_tid,
            "context": service_context_key(weft_harness.context),
        }
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10.0)
        process.close()


@pytest.mark.parametrize("persistent", [False, True])
def test_agent_workers_receive_runtime_identity(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    persistent: bool,
) -> None:
    """Both host agent entry points preserve identity before the model executes."""
    context = weft_harness.context
    tid = str(time.time_ns())
    identity = RuntimeIdentity(
        tid=tid,
        context=service_context_key(context),
        backend_name=context.backend_name,
        broker_target=context.broker_target,
        context_root=str(context.root),
        config=context.config,
    )
    monkeypatch.setattr(identity_module, "_runtime_identity", identity)
    runner = HostTaskRunner(
        target_type="agent",
        tid=tid,
        function_target=None,
        process_target=None,
        agent={
            "runtime": "llm",
            "model": "weft-runtime-identity-model",
            "conversation_scope": "per_task" if persistent else "per_message",
            "runtime_config": {"plugin_modules": [__name__]},
        },
        args=None,
        kwargs=None,
        env={},
        working_dir=None,
        timeout=20.0,
        limits=None,
        monitor_class=None,
        monitor_interval=None,
    )
    if persistent:
        session = runner.start_agent_session()
        try:
            session_outcome = session.execute("identity")
        finally:
            session.close()
        assert session_outcome.status == "ok", session_outcome.error
        value = session_outcome.value
    else:
        outcome = runner.run("identity")
        assert outcome.status == "ok", outcome.error
        value = outcome.value
    assert value is not None
    assert json.loads(value.aggregate_public_output()) == {
        "tid": tid,
        "context": identity.context,
    }


class RuntimeIdentityModel(Model):
    """Local model substitutes only the external LLM call, retaining agent plumbing."""

    model_id = "weft-runtime-identity-model"

    def execute(
        self,
        prompt: Prompt,
        stream: bool,
        response: Response,
        conversation: Any,
    ) -> Iterator[str]:
        del prompt, stream, response, conversation
        yield observe_runtime_identity(None)


@llm.hookimpl
def register_models(register: Callable[[Model], None]) -> None:
    """Register the local model through the real llm plugin boundary."""
    register(RuntimeIdentityModel())


def test_canonical_context_distinguishes_targets_and_namespaces(tmp_path: Path) -> None:
    """Display names and credentials cannot determine runtime routing authority."""
    left = BrokerTarget(
        backend_name="postgres",
        target="postgresql://weft:secret@host.test:5432/tasks",
        backend_options={"schema": "alpha"},
    )
    rotated_password = BrokerTarget(
        backend_name="postgres",
        target="postgresql://weft:new-secret@host.test:5432/tasks",
        backend_options={"schema": "alpha"},
    )
    other_schema = BrokerTarget(
        backend_name="postgres",
        target=left.target,
        backend_options={"schema": "beta"},
    )
    other_database = BrokerTarget(
        backend_name="postgres",
        target="postgresql://weft:secret@host.test:5432/other",
        backend_options=left.backend_options,
    )
    key = canonical_runtime_context(left)
    assert key == canonical_runtime_context(rotated_password)
    assert key != canonical_runtime_context(other_schema)
    assert key != canonical_runtime_context(other_database)
    assert "secret" not in key
    assert canonical_runtime_context(
        str(tmp_path / "dir" / ".." / "tasks.db")
    ) == canonical_runtime_context(str(tmp_path / "tasks.db"))


def test_owner_label_uses_full_tid_and_context_without_credentials() -> None:
    first = connection_owner_label("1791919490820769000", "postgres:context-a")
    second = connection_owner_label("1791919490820769001", "postgres:context-a")
    other = connection_owner_label("1791919490820769000", "postgres:context-b")
    assert first != second
    assert first != other
    assert len(first) <= 54
    assert all("!" <= character <= "~" for character in first)
    assert "context-a" not in first


@pytest.mark.parametrize(
    "changes",
    [
        {"connection_budget": 0},
        {"connection_budget": True},
        {"connection_budget": "8"},
        {"connection_owner": "contains space"},
        {"connection_owner": "x" * 55},
        {"connection_owner": "é"},
        {"submitter_tid": "123"},
        {"submitter_tid": 1791919490820769000},
        {"unsupported": "value"},
    ],
)
def test_admission_record_rejects_malformed_transport(changes: dict[str, Any]) -> None:
    """Malformed launch evidence cannot enter snapshot accounting as valid debt."""
    payload = {
        "connection_budget": 8,
        "connection_owner": "owner",
        "submitter_tid": None,
        **changes,
    }
    with pytest.raises(ValueError):
        AdmissionRecord.from_mapping(payload)


def test_runtime_identity_is_immutable_and_sqlite_needs_no_pg_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Dedicated processes may not change task identity or import an optional backend."""
    monkeypatch.setattr(identity_module, "_runtime_identity", None)
    target = str(tmp_path / "tasks.db")
    identity = RuntimeIdentity(
        tid="1791919490820769000",
        context=canonical_runtime_context(target),
        backend_name="sqlite",
        broker_target=target,
    )
    pg_loaded = "simplebroker_pg" in sys.modules
    install_runtime_identity(identity)
    install_runtime_identity(identity)
    assert identity_module.get_runtime_identity() is identity
    assert ("simplebroker_pg" in sys.modules) is pg_loaded
    with pytest.raises(FrozenInstanceError):
        identity.tid = "1791919490820769001"  # type: ignore[misc]
    with pytest.raises(RuntimeError, match="immutable"):
        install_runtime_identity(
            RuntimeIdentity(
                tid="1791919490820769001",
                context=identity.context,
                backend_name="sqlite",
            )
        )


def observe_owned_connections(_payload: Any) -> str:
    """Open real worker resources only after its identity is installed."""
    identity = identity_module.get_runtime_identity()
    assert identity is not None
    assert identity.admission is not None
    context = build_context(create_dirs=False, create_database=False)
    with context.queue("runtime.identity.probe", persistent=True) as queue:
        queue.write("bound")
        postgres = importlib.import_module("simplebroker_pg")
        # Idempotence after a real open proves the worker was bound early. A
        # previously unbound process is sealed at this point and must reject it.
        postgres.bind_connection_owner(identity.admission.connection_owner)
        usage = postgres.get_connection_usage(queue)
    return json.dumps(
        {
            "admission": identity.admission.to_dict(),
            "usage": usage,
            "worker_pid": os.getpid(),
        }
    )


def test_postgres_wrapper_and_worker_share_owner_and_snapshot_budget(
    broker_env: BrokerEnv,
    weft_harness: WeftTestHarness,
    workdir: Path,
) -> None:
    """Real PG connections credit the launch owner and task snapshots retain its budget."""
    context = weft_harness.context
    if context.backend_name != "postgres":
        pytest.skip("requires PostgreSQL attribution")
    target, make_queue = broker_env
    tid = str(time.time_ns())
    parent_tid = str(int(tid) - 1)
    admission = AdmissionRecord(
        8, connection_owner_label(tid, service_context_key(context)), parent_tid
    )
    spec = make_function_taskspec(
        tid,
        "tests.core.test_runtime_identity:observe_owned_connections",
        weft_context=str(workdir),
    )
    spec.metadata["admission"] = {"connection_budget": 1, "connection_owner": "forged"}
    make_queue(spec.io.inputs["inbox"]).write("payload")
    process = launch_task_process(
        Consumer,
        target,
        spec,
        config=context.config,
        admission=admission,
        submitter_tid=parent_tid,
    )
    assert process.pid is not None
    weft_harness.register_pid(process.pid, tid)
    try:
        process.join(30.0)
        assert not process.is_alive()
        assert process.exitcode == 0
        raw = make_queue(spec.io.outputs["outbox"]).read_one()
        assert raw is not None
        result = json.loads(raw)
        assert result["admission"] == admission.to_dict()
        assert result["usage"]["by_owner"][admission.connection_owner] >= 2
        mappings = [
            json.loads(row)
            for row in make_queue(task_state_queue_name(tid)).peek_generator()
        ]
        assert mappings
        assert all(row["admission"] == admission.to_dict() for row in mappings)
        assert all(row["submitter_tid"] == parent_tid for row in mappings)
        # Runtime handles may describe only the active worker. Every PG
        # snapshot separately retains the wrapper so replacement cannot release
        # its reopen budget while it is still finalizing after worker exit.
        assert all(
            process.pid in {item["pid"] for item in row["host_processes"]}
            for row in mappings
        )
        assert any(
            result["worker_pid"] in {item["pid"] for item in row["host_processes"]}
            for row in mappings
        )
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10.0)
        process.close()
