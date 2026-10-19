"""Public runtime evidence preserves uncertainty and input identity [PY-2], [MF-5]."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import psutil
import pytest
import weft_docker.plugin as docker_plugin

from simplebroker.ext import BrokerError
from tests.helpers.test_backend import prepare_project_root
from tests.helpers.weft_harness import WeftTestHarness
from weft._exceptions import CommandExecutionError, CommandUsageError, InvalidTID
from weft.client import RuntimeLivenessSnapshot, Task, WeftClient
from weft.commands import tasks
from weft.context import WeftContext, build_context
from weft.core.task_state import task_state_queue_name
from weft.ext import RunnerHandle
from weft.helpers import tid_short_form
from weft.liveness import host, registry

pytestmark = [pytest.mark.shared]
TID = "1779000000000000001"


@pytest.fixture
def runtime_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WeftContext:
    monkeypatch.setattr(registry, "_runtime_liveness_probes", {})
    return build_context(spec_context=prepare_project_root(tmp_path))


def _publish(
    ctx: WeftContext,
    *,
    authority: str = "runner",
    hostname: object = None,
    terminal: bool = False,
    handle_present: bool = True,
    runner: str = "public-observation-test",
    provider: str = "public-observation-test",
    processes: list[dict[str, object]] | None = None,
) -> int:
    handle = RunnerHandle(
        runner=runner,
        kind="process",
        id="original-runtime",
        control={"authority": authority},
        observations={"liveness_provider": provider, "host_processes": processes or []},
    )
    payload: dict[str, Any] = {
        "full": TID,
        "short": tid_short_form(TID),
        "terminal": terminal,
        "hostname": socket.gethostname() if hostname is None else hostname,
    }
    if handle_present:
        payload["runtime_handle"] = handle.to_dict()
    with ctx.queue(task_state_queue_name(TID)) as queue:
        return int(queue.write(json.dumps(payload)))


def test_explicit_runtime_probe_reports_live_with_original_provenance(
    runtime_context: WeftContext,
) -> None:
    """A public call must execute the registered authority, even for terminal state."""
    message_id = _publish(runtime_context, terminal=True)
    registry.register_runtime_liveness_probe(
        "public-observation-test", lambda *_: "live"
    )
    result = tasks.task_runtime_liveness(TID, context=runtime_context)
    assert isinstance(result, RuntimeLivenessSnapshot)
    assert (result.state, result.reason, result.source) == (
        "live",
        "runtime_live",
        "runner",
    )
    assert (result.runner, result.runtime_id, result.provider) == (
        "public-observation-test",
        "original-runtime",
        "public-observation-test",
    )
    assert result.snapshot_message_id == message_id
    assert result.check_started_at_ns > 0
    assert result.check_completed_at_ns > 0
    with pytest.raises(FrozenInstanceError):
        cast(Any, result).state = "dead"


@pytest.mark.parametrize(
    ("evidence", "state", "reason"),
    [
        ("live", "live", "runtime_live"),
        ("stale", "dead", "runtime_dead"),
        ("unknown", "unknown", "runtime_unresolved"),
    ],
)
@pytest.mark.parametrize("authority", ["runner", "external-supervisor"])
def test_provider_authority_maps_raw_evidence_without_lifecycle_override(
    runtime_context: WeftContext,
    evidence: Any,
    state: str,
    reason: str,
    authority: str,
) -> None:
    _publish(runtime_context, authority=authority, terminal=True)
    registry.register_runtime_liveness_probe(
        "public-observation-test", lambda *_: evidence
    )
    result = tasks.task_runtime_liveness(TID, context=runtime_context)
    assert (result.state, result.reason, result.source) == (state, reason, authority)


@pytest.mark.parametrize("failure", ["exception", "invalid"])
def test_provider_failure_is_unknown_with_stable_reason(
    runtime_context: WeftContext,
    failure: str,
) -> None:
    _publish(runtime_context)

    def callback(_handle: RunnerHandle, _budget: float) -> Any:
        if failure == "exception":
            raise TimeoutError("provider-secret")
        return ["invalid", "provider-secret"]

    registry.register_runtime_liveness_probe("public-observation-test", callback)
    result = tasks.task_runtime_liveness(TID, context=runtime_context)
    assert (result.state, result.reason) == ("unknown", "probe_failed")
    assert "provider-secret" not in repr(result)


@pytest.mark.parametrize("authority", ["host-pid", "runner", "external-supervisor"])
@pytest.mark.parametrize("hostname", ["foreign-host.invalid", "", 17])
def test_unproven_host_scope_preserves_identity_without_probe(
    runtime_context: WeftContext,
    monkeypatch: pytest.MonkeyPatch,
    authority: str,
    hostname: object,
) -> None:
    _publish(
        runtime_context,
        authority=authority,
        hostname=hostname,
        provider=" alias-provider ",
    )
    calls: list[float] = []

    def callback(_handle: RunnerHandle, budget: float) -> Any:
        calls.append(budget)
        return "live"

    blocked = hostname == "foreign-host.invalid" or authority == "host-pid"
    loaded: list[str] = []
    monkeypatch.setattr(tasks, "get_runner_plugin", lambda key: loaded.append(key))
    if not blocked:
        registry.register_runtime_liveness_probe("alias-provider", callback)
    result = tasks.task_runtime_liveness(TID, context=runtime_context)
    assert loaded == []
    assert (result.state, result.reason) == (
        ("unknown", "runtime_unresolved") if blocked else ("live", "runtime_live")
    )
    assert len(calls) == (0 if blocked else 1)
    assert result.provider == (None if authority == "host-pid" else "alias-provider")


@pytest.mark.parametrize(
    "case", ["no_row", "no_handle", "foreign_no_handle", "unsupported"]
)
def test_missing_or_unsupported_runtime_never_proves_death(
    runtime_context: WeftContext,
    case: str,
) -> None:
    if case != "no_row":
        _publish(
            runtime_context,
            authority="custom" if case == "unsupported" else "runner",
            handle_present=case == "unsupported",
            hostname="foreign-host.invalid" if case == "foreign_no_handle" else None,
        )
    result = tasks.task_runtime_liveness(TID, context=runtime_context)
    assert result.state == "unknown"
    assert result.reason == (
        "authority_unsupported"
        if case == "unsupported"
        else "runtime_handle_unavailable"
    )
    assert result.source == "none"
    assert result.provider is None
    if case != "unsupported":
        assert result.runner is None and result.runtime_id is None
    assert (result.snapshot_message_id is None) == (case == "no_row")


@pytest.mark.parametrize(
    "tid",
    [
        "T1779000000000000001",
        " 1779000000000000001",
        "1779",
        "0000000000000000000",
        "9999999999999999999",
        "١٧٧٩٠٠٠٠٠٠٠٠٠٠٠٠٠٠١",
    ],
)
def test_runtime_observation_rejects_noncanonical_or_out_of_range_tid(
    runtime_context: WeftContext,
    tid: str,
) -> None:
    with pytest.raises(InvalidTID):
        tasks.task_runtime_liveness(tid, context=runtime_context)


@pytest.mark.parametrize(
    "timeout", [True, False, 0.0, -1.0, float("inf"), float("nan"), "2", None]
)
def test_runtime_observation_rejects_invalid_budget(
    runtime_context: WeftContext,
    timeout: Any,
) -> None:
    _publish(runtime_context)
    calls: list[float] = []

    def callback(_handle: RunnerHandle, budget: float) -> Any:
        calls.append(budget)
        return "live"

    registry.register_runtime_liveness_probe("public-observation-test", callback)
    with pytest.raises(CommandUsageError):
        tasks.task_runtime_liveness(TID, context=runtime_context, timeout=timeout)
    assert calls == []


def test_namespace_and_fresh_handle_share_frozen_result_type(
    runtime_context: WeftContext,
) -> None:
    _publish(runtime_context)
    registry.register_runtime_liveness_probe(
        "public-observation-test", lambda *_: "live"
    )
    with WeftClient(context=runtime_context) as client:
        retained = client.task(TID)
        namespace = client.tasks.runtime_liveness(TID)
        handled = retained.runtime_liveness()
    with WeftClient(context=runtime_context) as fresh:
        reconstructed = Task(fresh, TID).runtime_liveness()
    for result in (namespace, handled, reconstructed):
        assert type(result) is RuntimeLivenessSnapshot
        assert (result.state, result.reason) == ("live", "runtime_live")
    assert namespace.snapshot_message_id == reconstructed.snapshot_message_id


@pytest.mark.parametrize(
    "mutation", ["replace", "remove", "malformed_tail", "unchanged"]
)
@pytest.mark.parametrize("late", [False, True])
def test_final_identity_fence_precedes_probe_outcome_and_keeps_original_provenance(
    runtime_context: WeftContext,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    late: bool,
) -> None:
    """Mutate real state at the provider boundary rather than mocking selected evidence."""
    selected = _publish(runtime_context)
    now = [10.0]
    monkeypatch.setattr(tasks, "_runtime_liveness_monotonic", lambda: now[0])
    wall = iter([900, 800])
    monkeypatch.setattr(tasks, "_runtime_liveness_time_ns", lambda: next(wall))
    budgets: list[float] = []

    def callback(_handle: RunnerHandle, budget: float) -> Any:
        budgets.append(budget)
        if mutation == "replace":
            _publish(runtime_context)
        elif mutation == "remove":
            with runtime_context.broker() as broker:
                broker.delete_message_ids(task_state_queue_name(TID), [selected])
        elif mutation == "malformed_tail":
            with runtime_context.queue(task_state_queue_name(TID)) as queue:
                queue.write("{malformed")
        now[0] += 2.0 if late else 0.25
        if mutation == "replace":
            raise RuntimeError("failure concurrent with replacement")
        return "stale"

    registry.register_runtime_liveness_probe("public-observation-test", callback)
    result = tasks.task_runtime_liveness(TID, context=runtime_context, timeout=1.0)
    expected = (
        ("unknown", "snapshot_changed")
        if mutation in {"replace", "remove"}
        else (("unknown", "probe_timeout") if late else ("dead", "runtime_dead"))
    )
    assert (result.state, result.reason) == expected
    assert result.snapshot_message_id == selected
    assert (result.check_started_at_ns, result.check_completed_at_ns) == (900, 800)
    assert budgets == [1.0]


@pytest.mark.parametrize("failure", [False, True])
def test_late_callback_result_or_exception_is_timeout(
    runtime_context: WeftContext,
    monkeypatch: pytest.MonkeyPatch,
    failure: bool,
) -> None:
    _publish(runtime_context)
    now = [0.0]
    monkeypatch.setattr(tasks, "_runtime_liveness_monotonic", lambda: now[0])

    def callback(_handle: RunnerHandle, _budget: float) -> Any:
        now[0] = 3.0
        if failure:
            raise RuntimeError("late failure")
        return "live"

    registry.register_runtime_liveness_probe("public-observation-test", callback)
    result = tasks.task_runtime_liveness(TID, context=runtime_context, timeout=2.0)
    assert (result.state, result.reason) == ("unknown", "probe_timeout")


@pytest.mark.parametrize("load_failure", [False, True])
def test_unavailable_provider_and_exhausted_preparation_budget(
    runtime_context: WeftContext,
    monkeypatch: pytest.MonkeyPatch,
    load_failure: bool,
) -> None:
    _publish(runtime_context)
    now = [0.0]
    monkeypatch.setattr(tasks, "_runtime_liveness_monotonic", lambda: now[0])

    def loader(_name: str) -> Any:
        if load_failure:
            raise ImportError("unavailable")
        now[0] = 3.0
        registry.register_runtime_liveness_probe(
            "public-observation-test", lambda *_: pytest.fail("callback after deadline")
        )
        return None

    monkeypatch.setattr(tasks, "get_runner_plugin", loader)
    result = tasks.task_runtime_liveness(TID, context=runtime_context, timeout=2.0)
    assert (result.state, result.reason) == (
        "unknown",
        "probe_unavailable" if load_failure else "probe_timeout",
    )


@pytest.mark.parametrize(
    "status,actual_time,denied,expected",
    [
        ("running", 5.0, False, "live"),
        ("zombie", 5.0, False, "dead"),
        ("running", 6.0, False, "dead"),
        ("running", 5.0, True, "unknown"),
        ("absent", 5.0, False, "dead"),
    ],
)
def test_public_host_identity_uses_real_os_adapter_truth_table(
    runtime_context: WeftContext,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    actual_time: float,
    denied: bool,
    expected: str,
) -> None:
    _publish(
        runtime_context,
        authority="host-pid",
        processes=[{"pid": 1234, "create_time": 5.0}],
    )

    def process(pid: int) -> Any:
        if status == "absent":
            raise psutil.NoSuchProcess(pid)
        if denied:
            raise psutil.AccessDenied(pid)
        return SimpleNamespace(
            create_time=lambda: actual_time, status=lambda: status, cmdline=list
        )

    monkeypatch.setattr(host.psutil, "Process", process)
    result = tasks.task_runtime_liveness(TID, context=runtime_context)
    assert result.state == expected
    assert (
        result.reason
        == {
            "live": "runtime_live",
            "dead": "runtime_dead",
            "unknown": "runtime_unresolved",
        }[expected]
    )
    assert result.source == "host-pid"
    assert result.provider is None


def test_host_missing_creation_time_is_unknown(
    runtime_context: WeftContext,
) -> None:
    _publish(
        runtime_context,
        authority="host-pid",
        processes=[{"pid": 1234, "create_time": None}],
    )
    result = tasks.task_runtime_liveness(TID, context=runtime_context)
    assert (result.state, result.reason) == ("unknown", "runtime_unresolved")


def test_host_probe_late_os_error_is_timeout(
    runtime_context: WeftContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _publish(
        runtime_context,
        authority="host-pid",
        processes=[{"pid": 1234, "create_time": 5.0}],
    )
    now = [0.0]
    monkeypatch.setattr(tasks, "_runtime_liveness_monotonic", lambda: now[0])

    def process(_pid: int) -> Any:
        now[0] = 3.0
        raise psutil.AccessDenied(_pid)

    monkeypatch.setattr(host.psutil, "Process", process)
    result = tasks.task_runtime_liveness(TID, context=runtime_context, timeout=2.0)
    assert (result.state, result.reason) == ("unknown", "probe_timeout")


def test_real_harness_owned_process_live_then_joined_exit(
    weft_harness: WeftTestHarness,
) -> None:
    """Popen pipe blocks until explicit input; wait reaps before checking death."""
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"],
        stdin=subprocess.PIPE,
    )
    try:
        created = psutil.Process(process.pid).create_time()
        weft_harness.register_pid(process.pid, create_time=created)
        _publish(
            weft_harness.context,
            authority="host-pid",
            processes=[{"pid": process.pid, "create_time": created}],
        )
        live = tasks.task_runtime_liveness(TID, context=weft_harness.context)
        assert (live.state, live.reason) == ("live", "runtime_live")
        assert process.stdin is not None
        process.stdin.write(b"x")
        process.stdin.close()
        process.wait(timeout=10)
        dead = tasks.task_runtime_liveness(TID, context=weft_harness.context)
        assert (dead.state, dead.reason) == ("dead", "runtime_dead")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        if process.stdin is not None:
            process.stdin.close()


def test_cold_installed_provider_entrypoint_reaches_sdk_boundary(
    runtime_context: WeftContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real entrypoint must register Docker before its callback inspects the SDK."""
    _publish(runtime_context, runner="wrapper", provider="docker")
    # Docker-backed wrappers retain their provider authority through an alias.
    with runtime_context.queue(task_state_queue_name(TID)) as queue:
        body = queue.peek_one()
        assert body is not None
        row = json.loads(body)
        row["runtime_handle"]["observations"]["container_runtime"] = "docker"
        row["runtime_handle"]["observations"]["container_id"] = "original-runtime"
        queue.write(json.dumps(row))
    requests: list[str] = []
    container = SimpleNamespace(attrs={"State": {"Running": True}}, reload=lambda: None)

    def get_container(identity: str) -> Any:
        requests.append(identity)
        return container

    @contextmanager
    def sdk_client(*, timeout: float = 10.0) -> Iterator[Any]:
        assert 0 < timeout <= 2.0
        yield SimpleNamespace(containers=SimpleNamespace(get=get_container))

    monkeypatch.setattr(docker_plugin, "_docker_client", sdk_client)
    result = tasks.task_runtime_liveness(TID, context=runtime_context)
    assert (result.state, result.reason, result.provider) == (
        "live",
        "runtime_live",
        "docker",
    )
    assert requests == ["original-runtime"]


@pytest.mark.parametrize("failure", [False, True])
def test_probe_releases_broker_loans_and_preserves_caller_session(
    runtime_context: WeftContext,
    monkeypatch: pytest.MonkeyPatch,
    failure: bool,
) -> None:
    _publish(runtime_context)
    original_broker = WeftContext.broker
    depth = [0]

    @contextmanager
    def tracked_broker(ctx: WeftContext) -> Iterator[Any]:
        with original_broker(ctx) as broker:
            depth[0] += 1
            try:
                yield broker
            finally:
                depth[0] -= 1

    monkeypatch.setattr(WeftContext, "broker", tracked_broker)

    def callback(_handle: RunnerHandle, _budget: float) -> Any:
        assert depth[0] == 0, "broker loan held during runtime probe"
        if failure:
            raise RuntimeError("controlled provider failure")
        return "live"

    registry.register_runtime_liveness_probe("public-observation-test", callback)
    with runtime_context.session():
        result = tasks.task_runtime_liveness(TID, context=runtime_context)
        with runtime_context.broker() as broker:
            assert broker.peek_one(task_state_queue_name(TID)) is not None
    assert result.reason == ("probe_failed" if failure else "runtime_live")
    assert depth == [0]


def test_final_broker_failure_remains_typed_instead_of_unknown(
    runtime_context: WeftContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _publish(runtime_context)
    original_broker = WeftContext.broker
    fail = [False]

    @contextmanager
    def failing_broker(ctx: WeftContext) -> Iterator[Any]:
        if fail[0]:
            raise BrokerError("final revalidation unavailable")
        with original_broker(ctx) as broker:
            yield broker

    monkeypatch.setattr(WeftContext, "broker", failing_broker)

    def callback(_handle: RunnerHandle, _budget: float) -> Any:
        fail[0] = True
        return "stale"

    registry.register_runtime_liveness_probe("public-observation-test", callback)
    with pytest.raises(CommandExecutionError):
        tasks.task_runtime_liveness(TID, context=runtime_context)


def test_ordinary_point_observers_gain_no_runtime_probes(
    runtime_context: WeftContext,
) -> None:
    _publish(runtime_context, terminal=True)
    calls: list[float] = []

    def callback(_handle: RunnerHandle, budget: float) -> Any:
        calls.append(budget)
        return "live"

    registry.register_runtime_liveness_probe("public-observation-test", callback)
    tasks.task_snapshot(TID, context=runtime_context)
    tasks.task_terminal_snapshot(TID, context=runtime_context)
    tasks.task_status(TID, context=runtime_context)
    assert calls == []
    assert tasks.task_runtime_liveness(TID, context=runtime_context).state == "live"
    assert len(calls) == 1


def test_uninstalled_provider_is_unknown_and_keeps_selected_key(
    runtime_context: WeftContext,
) -> None:
    _publish(runtime_context, provider="definitely-not-an-installed-weft-provider")
    result = tasks.task_runtime_liveness(TID, context=runtime_context)
    assert (result.state, result.reason) == ("unknown", "probe_unavailable")
    assert result.provider == "definitely-not-an-installed-weft-provider"


def test_host_dead_plus_unresolved_identity_remains_unknown(
    runtime_context: WeftContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _publish(
        runtime_context,
        authority="host-pid",
        processes=[
            {"pid": 1234, "create_time": 5.0},
            {"pid": 1235, "create_time": 5.0},
        ],
    )

    def process(pid: int) -> Any:
        if pid == 1234:
            raise psutil.NoSuchProcess(pid)
        raise psutil.AccessDenied(pid)

    monkeypatch.setattr(host.psutil, "Process", process)
    result = tasks.task_runtime_liveness(TID, context=runtime_context)
    assert (result.state, result.reason) == ("unknown", "runtime_unresolved")


def test_final_io_alone_does_not_expire_completed_probe(
    runtime_context: WeftContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _publish(runtime_context)
    now = [0.0]
    monkeypatch.setattr(tasks, "_runtime_liveness_monotonic", lambda: now[0])
    original_broker = WeftContext.broker
    probe_finished = [False]

    @contextmanager
    def slow_final_broker(ctx: WeftContext) -> Iterator[Any]:
        if probe_finished[0]:
            now[0] = 99.0
        with original_broker(ctx) as broker:
            yield broker

    monkeypatch.setattr(WeftContext, "broker", slow_final_broker)

    def callback(_handle: RunnerHandle, _budget: float) -> Any:
        now[0] = 0.25
        probe_finished[0] = True
        return "live"

    registry.register_runtime_liveness_probe("public-observation-test", callback)
    result = tasks.task_runtime_liveness(TID, context=runtime_context, timeout=1.0)
    assert (result.state, result.reason) == ("live", "runtime_live")


def test_budget_exhausted_by_initial_read_starts_no_probe(
    runtime_context: WeftContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _publish(runtime_context)
    now = [0.0]
    monkeypatch.setattr(tasks, "_runtime_liveness_monotonic", lambda: now[0])
    original_broker = WeftContext.broker

    @contextmanager
    def slow_initial_broker(ctx: WeftContext) -> Iterator[Any]:
        with original_broker(ctx) as broker:
            yield broker
        now[0] = 99.0

    monkeypatch.setattr(WeftContext, "broker", slow_initial_broker)
    registry.register_runtime_liveness_probe(
        "public-observation-test", lambda *_: pytest.fail("probe starts after budget")
    )
    result = tasks.task_runtime_liveness(TID, context=runtime_context, timeout=1.0)
    assert (result.state, result.reason) == ("unknown", "probe_timeout")
