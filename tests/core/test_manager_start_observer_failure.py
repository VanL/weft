"""Detached startup retains launch custody on observer loss [MA-3]."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from typing import Any

import pytest

from simplebroker.ext import BrokerError
from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_SERVICES_REGISTRY_QUEUE, WEFT_SPAWN_REQUESTS_QUEUE
from weft._exceptions import ManagerStartFailed
from weft.context import WeftContext
from weft.core import manager_runtime
from weft.core.service_convergence import build_manager_service_payload
from weft.helpers import pid_is_live, process_create_time, terminate_process_tree

pytestmark = [pytest.mark.shared]


@pytest.fixture
def owned_manager_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[WeftContext, list[manager_runtime.DetachedManagerLaunch]]]:
    """Keep real detached launcher custody; substitute only its executable work."""
    launches: list[manager_runtime.DetachedManagerLaunch] = []
    original_launch = manager_runtime._launch_detached_manager
    original_abort = manager_runtime._abort_unconfirmed_launcher
    original_monitor = manager_runtime.QueueChangeMonitor

    def launch(
        context: WeftContext, invocation: manager_runtime.ManagerRuntimeInvocation
    ) -> manager_runtime.DetachedManagerLaunch:
        result = original_launch(context, invocation)
        launches.append(result)
        return result

    monkeypatch.setattr(
        manager_runtime,
        "_build_manager_process_command",
        lambda context, invocation: [
            sys.executable,
            "-c",
            "import threading; threading.Event().wait()",
        ],
    )
    monkeypatch.setattr(manager_runtime, "_launch_detached_manager", launch)
    with WeftTestHarness() as harness:
        try:
            yield harness.context, launches
        finally:
            # The operational fault belongs to startup, not harness teardown.
            monkeypatch.setattr(manager_runtime, "QueueChangeMonitor", original_monitor)
            for result in launches:
                if result.launcher_process.poll() is None:
                    original_abort(result.launcher_process)
                # A SUCCESS handoff deliberately leaves the runtime detached.
                terminate_process_tree(result.pid, timeout=1.0)


@pytest.mark.parametrize(
    "phase,error_type",
    [
        ("registration", BrokerError),
        ("first_wait", BrokerError),
        ("settlement", BrokerError),
        ("registration", OSError),
        ("registration", RuntimeError),
    ],
    ids=[
        "registration-broker",
        "first-wait-broker",
        "settlement-broker",
        "registration-oserror",
        "registration-runtimeerror",
    ],
)
def test_start_manager_reaps_unconfirmed_launch_on_observer_failure(
    owned_manager_launch: tuple[
        WeftContext, list[manager_runtime.DetachedManagerLaunch]
    ],
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    error_type: type[Exception],
) -> None:
    """Observer loss must not abandon an unacknowledged, live detached child."""
    context, launches = owned_manager_launch
    failure = error_type(f"registry observer failed during {phase}")
    monitors = 0
    original_launch = manager_runtime._launch_detached_manager

    def launch(
        context: WeftContext, invocation: manager_runtime.ManagerRuntimeInvocation
    ) -> manager_runtime.DetachedManagerLaunch:
        result = original_launch(context, invocation)
        if phase == "settlement":
            # Expire readiness only after the real first-event handshake.
            monkeypatch.setattr(manager_runtime, "MANAGER_STARTUP_TIMEOUT_SECONDS", 0)
        return result

    class FailedObservation:
        def __init__(self, queues: Any, *, config: Any) -> None:
            nonlocal monitors
            monitors += 1
            if phase == "registration":
                raise failure

        def wait(self, timeout: float | None = None) -> bool:
            raise failure

        def close(self) -> None:
            pass

    monkeypatch.setattr(manager_runtime, "_launch_detached_manager", launch)
    monkeypatch.setattr(manager_runtime, "QueueChangeMonitor", FailedObservation)
    with pytest.raises(ManagerStartFailed, match="registry observer failed") as error:
        manager_runtime.start_manager(context)
    assert error.value.__cause__ is failure
    assert monitors == (2 if phase == "settlement" else 1)
    assert len(launches) == 1
    result = launches[0]
    assert result.launcher_process.returncode is not None
    assert not pid_is_live(result.pid)


def test_start_manager_keeps_handed_off_runtime_on_settlement_observer_failure(
    owned_manager_launch: tuple[
        WeftContext, list[manager_runtime.DetachedManagerLaunch]
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An acknowledgement cleanup error cannot revoke an already sent SUCCESS."""
    context, launches = owned_manager_launch
    failure = BrokerError("post-ack registry observer failed")
    original_launch = manager_runtime._launch_detached_manager
    original_ack = manager_runtime._acknowledge_manager_launch_success

    def launch(
        context: WeftContext, invocation: manager_runtime.ManagerRuntimeInvocation
    ) -> manager_runtime.DetachedManagerLaunch:
        result = original_launch(context, invocation)
        payload = build_manager_service_payload(
            context=context,
            tid=invocation.tid,
            name="manager",
            status="active",
            queues={"requests": WEFT_SPAWN_REQUESTS_QUEUE},
            runtime_handle={
                "runner": "host",
                "kind": "process",
                "id": str(result.pid),
                "control": {"authority": "host-pid"},
                "observations": {
                    "host_processes": [
                        {
                            "pid": result.pid,
                            "create_time": process_create_time(result.pid),
                        }
                    ]
                },
                "metadata": {},
            },
        )
        with context.queue(WEFT_SERVICES_REGISTRY_QUEUE) as registry:
            registry.write(json.dumps(payload))
        return result

    def acknowledge(result: manager_runtime.DetachedManagerLaunch) -> None:
        original_ack(result)
        raise manager_runtime._ManagerLaunchAcknowledgementError(
            "acknowledgement cleanup failed after SUCCESS", success_signal_sent=True
        )

    class FailedObservation:
        def __init__(self, queues: Any, *, config: Any) -> None:
            pass

        def wait(self, timeout: float | None = None) -> bool:
            raise failure

        def close(self) -> None:
            pass

    monkeypatch.setattr(manager_runtime, "_launch_detached_manager", launch)
    monkeypatch.setattr(
        manager_runtime, "_acknowledge_manager_launch_success", acknowledge
    )
    monkeypatch.setattr(manager_runtime, "QueueChangeMonitor", FailedObservation)
    with pytest.raises(
        ManagerStartFailed, match="post-ack registry observer failed"
    ) as error:
        manager_runtime.start_manager(context)
    assert error.value.__cause__ is failure
    assert len(launches) == 1
    result = launches[0]
    assert result.launcher_process.returncode == 0
    assert pid_is_live(result.pid)


def test_start_manager_retains_observer_error_when_abort_cleanup_fails(
    owned_manager_launch: tuple[
        WeftContext, list[manager_runtime.DetachedManagerLaunch]
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Secondary cleanup diagnostics must not replace the startup failure."""
    context, launches = owned_manager_launch
    failure = BrokerError("primary registry observer failure")

    def fail_registration(*args: Any, **kwargs: Any) -> Any:
        raise failure

    def fail_cleanup(*args: Any, **kwargs: Any) -> Any:
        raise OSError("secondary detached cleanup failure")

    monkeypatch.setattr(manager_runtime, "QueueChangeMonitor", fail_registration)
    monkeypatch.setattr(manager_runtime, "_abort_unconfirmed_launcher", fail_cleanup)
    with pytest.raises(
        ManagerStartFailed, match="primary registry observer failure"
    ) as error:
        manager_runtime.start_manager(context)
    assert error.value.__cause__ is failure
    diagnostics = "\n".join(getattr(failure, "__notes__", []))
    diagnostics += "\n".join(getattr(error.value, "__notes__", []))
    assert "secondary detached cleanup failure" in diagnostics
    assert len(launches) == 1


def test_stop_manager_observer_failure_cannot_confirm_or_force_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken observer is neither exit proof nor permission to escalate."""
    failure = BrokerError("stop registry observer failed")
    tid = "1780000000000000055"

    class FailedObservation:
        def __init__(self, queues: Any, *, config: Any) -> None:
            pass

        def wait(self, timeout: float | None = None) -> bool:
            raise failure

        def close(self) -> None:
            pass

    monkeypatch.setattr(manager_runtime, "QueueChangeMonitor", FailedObservation)
    monkeypatch.setattr(
        manager_runtime,
        "terminate_process_tree",
        lambda *args, **kwargs: pytest.fail("observer failure must not force stop"),
    )
    monkeypatch.setattr(
        manager_runtime,
        "_mark_manager_stopped",
        lambda *args, **kwargs: pytest.fail("observer failure is not stopped proof"),
    )
    with WeftTestHarness() as harness:
        with pytest.raises(BrokerError, match="stop registry observer failed") as error:
            manager_runtime.stop_manager(
                harness.context,
                {"tid": tid, "status": "active"},
                timeout=10.0,
                force=True,
            )
        assert error.value is failure
        with harness.context.queue(f"T{tid}.ctrl_in") as control:
            message = control.peek_one()
        assert message is not None
        assert json.loads(str(message))["command"] == "STOP"
