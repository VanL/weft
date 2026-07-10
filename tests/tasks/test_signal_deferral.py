"""Regression coverage for deferred task termination-signal handling.

Spec references:
- docs/specifications/05-Message_Flow_and_State.md [MF-3]
- docs/specifications/07-System_Invariants.md [IMPL.10]
- docs/specifications/07-System_Invariants.md [STATE.1], [QUEUE.6], [OBS.1]

Background
----------
``weft/core/launcher.py``'s installed SIGTERM/SIGINT/SIGUSR1 handler used to
call ``task.handle_termination_signal()`` inline, inside the interrupted
Python signal frame. ``handle_termination_signal`` performs broker queue
writes (state-change reports, terminal envelopes, reserved-queue policy
application). SimpleBroker's runner lock is non-reentrant and its
transactions can span multiple Python calls, so a signal landing mid-SQL
could self-deadlock the process or leave a transaction half-applied.

The fix (Task A1) moves only the *call site*: the installed handler now
calls ``BaseTask.note_termination_signal``, which only appends the signum
to an in-memory source mailbox. The task
run loop (``BaseTask.process_once`` / ``Consumer.process_once``) checks for
a pending signum at the top of every turn and, if present, invokes the
existing (unmodified) ``handle_termination_signal`` from ordinary Python
call context -- never from inside a signal frame.

Why the "signal lands mid-commit" race is structurally excluded
-----------------------------------------------------------------
Before this fix, the dangerous window was "a signal arrives while a broker
transaction owned by the *signal-interrupted* frame is in flight," because
the handler itself re-entered the broker from inside that frame. After this
fix, the handler never touches the broker: it only appends a plain source
entry to an in-memory deque, which is safe to do from any point in the
target thread's execution (including between arbitrary bytecode
instructions) because CPython signal handlers run between bytecodes on the
main thread, and deque append is atomic. There is therefore no deterministic
way to construct a variant of
"signal during commit, full result winds up in a cancelled task's outbox"
against the fixed code: the only broker-visible work now happens from
``process_once()``, called from ordinary (non-signal) control flow, so it
can never be re-entered by the handler. The unit test below asserts this
directly: invoking the installed handler performs no queue I/O at all.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

import pytest

from simplebroker import Queue
from tests.helpers.test_backend import prepare_project_root
from tests.helpers.weft_harness import WeftTestHarness
from tests.tasks.test_task_execution import make_command_taskspec
from weft._constants import (
    QUEUE_RESERVED_SUFFIX,
    TASK_PROCESS_POLL_INTERVAL,
    WEFT_GLOBAL_LOG_QUEUE,
)
from weft.commands import tasks as task_cmd
from weft.context import build_context
from weft.core import launcher as launcher_module
from weft.core.launcher import _install_signal_handlers
from weft.core.tasks import Consumer
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec

pytestmark = [pytest.mark.shared]

TERMINAL_EVENTS = {
    "work_completed",
    "work_failed",
    "work_timeout",
    "work_limit_violation",
    "task_signal_stop",
    "task_signal_kill",
}


def _drain(queue: Queue) -> list[str]:
    items: list[str] = []
    while True:
        raw = queue.read_one()
        if raw is None:
            break
        items.append(raw)
    return items


def _peek_json_payloads(queue: Queue, *, limit: int = 50) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for raw in queue.peek_many(limit=limit) or []:
        try:
            payloads.append(json.loads(raw))
        except (TypeError, ValueError):
            continue
    return payloads


def _build_long_running_spec(tid: str, working_dir: Path) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="signal-deferral-long-running",
        spec=SpecSection(
            type="command",
            process_target=sys.executable,
            args=["-c", "import time; time.sleep(30)"],
            timeout=60.0,
            working_dir=str(working_dir),
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
        state=StateSection(),
    )


def _wait_for_running(root: Path, tid: str, *, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    latest = None
    while time.time() < deadline:
        latest = task_cmd.task_status(tid, context_path=root)
        if latest is not None and latest.status == "running":
            return
        time.sleep(0.02)
    raise AssertionError(
        f"Timed out waiting for running status for {tid}; latest={latest!r}"
    )


def _wait_for_process_exit(process: Any, *, timeout: float = 10.0) -> bool:
    pid = getattr(process, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        return True
    deadline = time.time() + timeout
    join = getattr(process, "join", None)
    while time.time() < deadline:
        if callable(join):
            join(timeout=0.05)
        if getattr(process, "exitcode", None) is not None:
            return True
        time.sleep(0.02)
    return False


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX signal delivery required for task-process SIGTERM deferral",
)
def test_sigterm_on_task_process_produces_exactly_one_terminal_event(
    tmp_path: Path,
) -> None:
    """SIGTERM against a real, spawned task process with active work.

    Verifies (Spec: [STATE.1], [QUEUE.6], [OBS.1]):
    (a) exactly one terminal event lands in ``weft.log.tasks``,
    (b) the reserved-queue KEEP policy is honored (message stays reserved),
    (c) the process exits within a bounded deadline.

    The task process is spawned via ``launch_task_process`` (real
    ``multiprocessing`` spawn context, real OS process) running a real
    long-lived subprocess work item, so the SIGTERM lands in the actual
    ``_handle_signal`` installed by ``weft.core.launcher`` -- exercising the
    deferred seam end to end, not a simulation of it.
    """

    root = prepare_project_root(tmp_path / "runtime-root")
    context = build_context(spec_context=root)
    tid = str(time.time_ns())
    spec = _build_long_running_spec(tid, root)

    inbox = context.queue(spec.io.inputs["inbox"], persistent=True)
    log_queue = context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True)
    reserved_queue = context.queue(f"T{tid}.{QUEUE_RESERVED_SUFFIX}", persistent=True)
    _drain(log_queue)

    # A payload of exactly ``{}`` is treated as a structural "start token"
    # sentinel by Consumer._is_start_token and forces reserved-policy CLEAR
    # regardless of the configured policy; use a real (non-empty) work
    # envelope so the configured KEEP policy is actually exercised.
    inbox.write(json.dumps({"args": []}))

    process = launcher_module.launch_task_process(
        Consumer,
        context.broker_target,
        spec,
        config=context.config,
    )
    try:
        _wait_for_running(root, tid)

        assert isinstance(process.pid, int)
        os.kill(process.pid, signal.SIGTERM)

        deadline = time.time() + 15.0
        latest = None
        while time.time() < deadline:
            latest = task_cmd.task_status(tid, context_path=root)
            if latest is not None and latest.status in {"cancelled", "killed"}:
                break
            time.sleep(0.02)
        assert latest is not None and latest.status in {
            "cancelled",
            "killed",
        }, f"task did not reach a terminal state in time; latest={latest!r}"

        assert _wait_for_process_exit(process, timeout=10.0), (
            "task process did not exit within the bounded deadline after SIGTERM"
        )

        events = _peek_json_payloads(log_queue)
        terminal_events_seen = [e for e in events if e.get("event") in TERMINAL_EVENTS]
        assert len(terminal_events_seen) == 1, (
            f"expected exactly one terminal event, got {terminal_events_seen!r} "
            f"(all events: {[e.get('event') for e in events]!r})"
        )

        # Default reserved_policy_on_stop is KEEP: the reserved message must
        # still be present for inspection (Spec: [QUEUE.6]).
        assert reserved_queue.peek_one() is not None
    finally:
        if getattr(process, "is_alive", None) and process.is_alive():
            process.terminate()
        join = getattr(process, "join", None)
        if callable(join):
            join(timeout=2.0)
        for queue in (inbox, log_queue, reserved_queue):
            close = getattr(queue, "close", None)
            if callable(close):
                close()


def test_installed_signal_handler_only_records_state_no_broker_call() -> None:
    """The installed signal handler must never touch the broker.

    This is the deferral-seam unit proof: invoke the *actual* handler
    installed by ``_install_signal_handlers`` (retrieved via
    ``signal.getsignal``) directly -- as CPython would when delivering a
    real signal -- against a stand-in task object that records whether
    ``handle_termination_signal`` (the broker-touching method) was ever
    called. Only ``note_termination_signal`` (plain in-memory state) may be
    invoked from the handler.
    """

    class RecordingTask:
        def __init__(self) -> None:
            self.noted: list[int] = []
            self.handled: list[int] = []
            self._stop_event = None

        def note_termination_signal(self, signum: int) -> None:
            self.noted.append(signum)

        def handle_termination_signal(self, signum: int) -> None:
            # Broker-touching in real tasks; must never be called from the
            # signal frame itself.
            self.handled.append(signum)

    task = RecordingTask()
    original_handler = signal.getsignal(signal.SIGTERM)
    try:
        _install_signal_handlers(task)
        installed = signal.getsignal(signal.SIGTERM)
        assert installed is not None
        # Invoke exactly as CPython's signal machinery would.
        installed(signal.SIGTERM, None)
    finally:
        signal.signal(signal.SIGTERM, original_handler)

    assert task.noted == [signal.SIGTERM]
    assert task.handled == []


def test_note_termination_signal_kill_class_outranks_later_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KILL-class (SIGUSR1) must not be downgraded by a later STOP-class signal."""

    sigusr1 = getattr(signal, "SIGUSR1", None)
    if sigusr1 is None:
        pytest.skip("platform has no SIGUSR1")

    # A minimal BaseTask-like harness is unnecessary here: exercise the
    # method directly against a real Consumer instance's in-memory state
    # without touching the broker.
    tid = str(time.time_ns())
    spec = make_command_taskspec(tid, sys.executable, args=["-c", "pass"])

    with WeftTestHarness() as harness:
        context = harness.context
        task = Consumer(context.broker_target, spec, config=context.config)
        try:
            applied: list[tuple[int, bool]] = []
            monkeypatch.setattr(
                task,
                "_apply_termination_request",
                lambda signum, *, parent_lost: applied.append((signum, parent_lost)),
            )
            task.note_termination_signal(sigusr1)
            task.note_termination_signal(signal.SIGTERM)
            task.process_once()

            assert applied == [(sigusr1, False)]
            assert task._has_pending_termination_request() is False
        finally:
            task.stop(join=False)


def test_note_termination_signal_last_stop_class_signal_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Among STOP-class signals, the most recently observed one is retained."""

    tid = str(time.time_ns())
    spec = make_command_taskspec(tid, sys.executable, args=["-c", "pass"])

    with WeftTestHarness() as harness:
        context = harness.context
        task = Consumer(context.broker_target, spec, config=context.config)
        try:
            applied: list[tuple[int, bool]] = []
            monkeypatch.setattr(
                task,
                "_apply_termination_request",
                lambda signum, *, parent_lost: applied.append((signum, parent_lost)),
            )
            task.note_termination_signal(signal.SIGINT)
            task.note_termination_signal(signal.SIGTERM)
            task.process_once()

            assert applied == [(signal.SIGTERM, False)]
            assert task._has_pending_termination_request() is False
        finally:
            task.stop(join=False)


def test_pending_source_snapshot_preserves_arrivals_during_drain(
    broker_env,
    unique_tid_signal_deferral: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Snapshot draining must leave concurrently appended sources for next turn."""

    sigusr1 = getattr(signal, "SIGUSR1", None)
    if sigusr1 is None:
        pytest.skip("platform has no SIGUSR1")

    db_path, _make_queue = broker_env
    task = Consumer(
        db_path,
        make_command_taskspec(
            unique_tid_signal_deferral,
            sys.executable,
            args=["-c", "pass"],
        ),
    )
    applied: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        task,
        "_apply_termination_request",
        lambda signum, *, parent_lost: applied.append((signum, parent_lost)),
    )
    task.note_termination_signal(signal.SIGTERM)

    class InjectingDeque(deque[tuple[str, int | None]]):
        injected = False

        def popleft(self) -> tuple[str, int | None]:
            source = super().popleft()
            if not self.injected:
                self.injected = True
                task.note_termination_signal(sigusr1)
                task.note_parent_loss()
            return source

    task._pending_termination_sources = InjectingDeque(
        task._pending_termination_sources
    )

    try:
        task.process_once()

        assert applied == [(signal.SIGTERM, False)]
        assert task._has_pending_termination_request() is True
        assert list(task._pending_termination_sources) == [
            ("signal", sigusr1),
            ("parent_loss", None),
        ]

        task.process_once()

        assert applied == [
            (signal.SIGTERM, False),
            (sigusr1, True),
        ]
        assert task._has_pending_termination_request() is False
    finally:
        task.stop(join=False)


def test_process_once_applies_pending_signal_and_clears_it(
    broker_env, unique_tid_signal_deferral: str
) -> None:
    """The run loop's ``process_once`` applies a noted signal exactly once."""

    db_path, _make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid_signal_deferral,
        sys.executable,
        args=["-c", "import time; time.sleep(5)"],
    )
    task = Consumer(db_path, spec)
    inbox = _make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": []}))

    try:
        task.process_once()
        assert task.taskspec.state.status == "running"

        task.note_termination_signal(signal.SIGTERM)
        assert task._has_pending_termination_request() is True
        assert task._external_stop_handled is False

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            task.process_once()
            if task.taskspec.state.status == "cancelled":
                break
            task.wait_for_activity(timeout=TASK_PROCESS_POLL_INTERVAL)

        assert task.taskspec.state.status == "cancelled"
        assert task._has_pending_termination_request() is False

        # A standalone process_once() does not own the outer lifecycle loop;
        # the same owner may inspect another bounded stopping turn before the
        # caller explicitly finalizes.
        noted_before = task._external_stop_handled
        task.process_once()
        assert task._external_stop_handled == noted_before
    finally:
        task.stop(join=False)


@pytest.fixture
def unique_tid_signal_deferral() -> str:
    return str(time.time_ns())
