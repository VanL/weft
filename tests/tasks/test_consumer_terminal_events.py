"""Consumer terminal outcome event-count regressions."""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import time
from typing import Any

import psutil
import pytest

from simplebroker import BrokerTarget, Queue
from tests.helpers.reactor_driver import drive_until
from tests.helpers.typing import BrokerEnv
from tests.helpers.weft_harness import WeftTestHarness
from tests.taskspec import fixtures
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.commands.result import await_task_result
from weft.core.runners import host as host_module
from weft.core.tasks import Consumer
from weft.core.tasks.base import TaskReactorLifecycle
from weft.core.tasks.runner import RunnerOutcome
from weft.core.taskspec import (
    IOSection,
    ReservedPolicy,
    SpecSection,
    StateSection,
    TaskSpec,
)

pytestmark = [pytest.mark.shared]


def _terminal_events(db_path: BrokerTarget, tid: str) -> list[str]:
    queue = Queue(WEFT_GLOBAL_LOG_QUEUE, db_path=db_path, persistent=False)
    try:
        events: list[str] = []
        for raw, _timestamp in queue.peek_generator(with_timestamps=True):
            payload = json.loads(raw)
            if isinstance(payload, dict) and payload.get("tid") == tid:
                event = payload.get("event")
                if isinstance(event, str):
                    events.append(event)
        return events
    finally:
        queue.close()


@pytest.mark.parametrize(
    ("status", "error", "expected_event"),
    [
        ("timeout", "too slow", "work_timeout"),
        ("limit", "memory limit", "work_limit_violation"),
        ("error", "boom", "work_failed"),
    ],
)
def test_consumer_terminal_outcome_emits_one_state_event(
    broker_env: BrokerEnv,
    status: str,
    error: str,
    expected_event: str,
) -> None:
    db_path, _ = broker_env
    taskspec = fixtures.create_minimal_taskspec()
    task = Consumer(db_path, taskspec)
    try:
        task.taskspec.mark_running()
        outcome = RunnerOutcome(
            status=status,
            value=None,
            error=error,
            stdout=None,
            stderr=None,
            returncode=None,
            duration=0.01,
        )

        with pytest.raises((RuntimeError, TimeoutError)):
            task._ensure_outcome_ok(outcome, timestamp=None, metrics_payload=None)

        events = _terminal_events(db_path, task.tid)
        assert events.count(expected_event) == 1
    finally:
        task.cleanup()


def test_consumer_unknown_runner_outcome_status_fails_task(
    broker_env: BrokerEnv,
) -> None:
    db_path, _ = broker_env
    taskspec = fixtures.create_minimal_taskspec()
    task = Consumer(db_path, taskspec)
    try:
        task.taskspec.mark_running()
        outcome = RunnerOutcome(
            status="nonsense",
            value=None,
            error=None,
            stdout=None,
            stderr=None,
            returncode=None,
            duration=0.01,
        )

        with pytest.raises(RuntimeError, match="unsupported runner outcome status"):
            task._ensure_outcome_ok(outcome, timestamp=None, metrics_payload=None)

        assert task.taskspec.state.status == "failed"
        assert "nonsense" in (task.taskspec.state.error or "")
        events = _terminal_events(db_path, task.tid)
        assert events.count("work_failed") == 1
    finally:
        task.cleanup()


@pytest.mark.parametrize("reserved_policy", [ReservedPolicy.KEEP, ReservedPolicy.CLEAR])
def test_monitor_validation_failure_finalizes_consumer_and_reaps_command(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
    reserved_policy: ReservedPolicy,
) -> None:
    """A failed task must not leave its post-spawn acquisition running [RM-5.1]."""
    db_path, make_queue = broker_env
    unique_tid = str(time.time_ns())
    spec = TaskSpec(
        tid=unique_tid,
        name="invalid-command-monitor",
        spec=SpecSection(
            type="command",
            process_target=sys.executable,
            args=["-c", "import time; time.sleep(30)"],
            monitor_class="builtins.str",
            cleanup_on_exit=False,
            reserved_policy_on_error=reserved_policy,
        ),
        io=IOSection(
            inputs={"inbox": f"T{unique_tid}.inbox"},
            outputs={"outbox": f"T{unique_tid}.outbox"},
            control={
                "ctrl_in": f"T{unique_tid}.ctrl_in",
                "ctrl_out": f"T{unique_tid}.ctrl_out",
            },
        ),
        state=StateSection(),
    )
    task = Consumer(db_path, spec)
    children: list[subprocess.Popen[str]] = []
    original_supervisor = host_module.run_monitored_subprocess

    def record_acquired_command(**kwargs: Any) -> RunnerOutcome:
        process = kwargs["process"]
        children.append(process)
        assert process.poll() is None, "command exited before monitor validation"
        return original_supervisor(**kwargs)

    monkeypatch.setattr(
        host_module, "run_monitored_subprocess", record_acquired_command
    )
    try:
        with make_queue(spec.io.inputs["inbox"]) as inbox:
            inbox.write("work")
        with task.drive_scope():
            drive_until(
                lambda: task.taskspec.state.status,
                lambda status: status == "failed",
                step=task.process_once,
                wait=task.wait_for_activity,
                timeout=10.0,
            )
        assert "BaseResourceMonitor subclass" in (task.taskspec.state.error or "")
        assert _terminal_events(db_path, unique_tid).count("work_failed") == 1
        assert task._task_lifecycle is TaskReactorLifecycle.CLOSED
        with make_queue(f"T{unique_tid}.reserved") as reserved:
            assert reserved.has_pending() is (reserved_policy is ReservedPolicy.KEEP)
        assert children, "monitor validation must occur after actual process creation"
        for child in children:
            # Do not let the assertion reap a child the supervisor failed to wait for.
            assert child.returncode is not None, (
                "failed Consumer did not reap its owned command"
            )
    finally:
        task.cleanup()
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5.0)


@pytest.mark.parametrize(
    ("payload", "inherited", "returncode"),
    [
        pytest.param("root output", True, 0, id="inherited-output"),
        pytest.param("", True, 0, id="inherited-empty"),
        pytest.param("root output", False, 0, id="physical-eof"),
        pytest.param("root output", True, 7, id="failed-root-no-logical-seal"),
    ],
)
def test_live_command_success_seals_public_result_before_completion(
    weft_harness: WeftTestHarness,
    payload: str,
    inherited: bool,
    returncode: int,
) -> None:
    """Successful work seals its logical stream independently of pipe EOF [MF-3]."""
    root = weft_harness.root
    ready = root / "descendant.json"
    release = root / "release-descendant"
    descendant_source = (
        "import json,pathlib,psutil,time\n"
        f"ready=pathlib.Path({str(ready)!r}); release=pathlib.Path({str(release)!r})\n"
        "me=psutil.Process(); tmp=ready.with_suffix('.tmp')\n"
        "tmp.write_text(json.dumps({'pid':me.pid,'created':me.create_time()})); tmp.replace(ready)\n"
        "deadline=time.monotonic()+30\n"
        "while not release.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
    )
    script = "import pathlib,subprocess,sys,time\n"
    if inherited:
        script += (
            f"subprocess.Popen([sys.executable,'-c',{descendant_source!r}])\n"
            f"ready=pathlib.Path({str(ready)!r}); deadline=time.monotonic()+5\n"
            "while not ready.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
            "assert ready.exists()\n"
        )
    script += (
        f"sys.stdout.write({payload!r}); sys.stdout.flush()\n"
        "sys.stderr.write('diagnostic'); sys.stderr.flush()\n"
        f"sys.exit({returncode})\n"
    )
    tid = str(time.time_ns())
    spec = TaskSpec(
        tid=tid,
        name="logical-stream-completion",
        spec=SpecSection(
            type="command",
            process_target=sys.executable,
            args=["-c", script],
            stream_output=True,
            cleanup_on_exit=False,
            timeout=5.0,
            weft_context=str(root),
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
        state=StateSection(),
    )
    task = Consumer(
        weft_harness.context.broker_target,
        spec,
        config=weft_harness.context.config,
    )
    descendant: psutil.Process | None = None
    try:
        if returncode:
            with pytest.raises(RuntimeError, match="Command exited with 7"):
                task.run_work_item({})
        else:
            assert task.run_work_item({}) == payload
        if inherited:
            identity = json.loads(ready.read_text())
            descendant = psutil.Process(identity["pid"])
            assert descendant.create_time() == identity["created"]
            assert (
                descendant.is_running() and descendant.status() != psutil.STATUS_ZOMBIE
            )

        with weft_harness.context.queue(f"T{tid}.outbox") as outbox:
            frames = [
                (json.loads(raw), timestamp)
                for raw, timestamp in outbox.peek_generator(with_timestamps=True)
            ]
        for stream in ("stdout", "stderr"):
            stream_frames = [frame for frame, _ in frames if frame["stream"] == stream]
            assert [frame["chunk"] for frame in stream_frames] == list(
                range(len(stream_frames))
            )
            assert sum(frame["final"] for frame in stream_frames) == (
                0 if returncode else 1
            )
        assert (
            "".join(frame["data"] for frame, _ in frames if frame["stream"] == "stderr")
            == "diagnostic"
        )
        if not returncode:
            if inherited:
                final_rows = {
                    frame["stream"]: timestamp
                    for frame, timestamp in frames
                    if frame["final"]
                }
                assert final_rows["stderr"] < final_rows["stdout"]
            with weft_harness.context.queue(f"T{tid}.ctrl_out") as ctrl_out:
                completions = [
                    timestamp
                    for raw, timestamp in ctrl_out.peek_generator(with_timestamps=True)
                    if json.loads(raw).get("type") == "terminal"
                ]
            assert completions
            assert max(
                timestamp for frame, timestamp in frames if frame["final"]
            ) < min(completions)
        result = await_task_result(
            weft_harness.context,
            tid,
            timeout=2.0,
            _accepted_taskspec=task.taskspec,
        )
        assert result.status == ("failed" if returncode else "completed")
        assert result.value == (None if returncode else payload)
    finally:
        release.touch()
        task.cleanup()
        if descendant is None and ready.exists():
            identity = json.loads(ready.read_text())
            with contextlib.suppress(psutil.NoSuchProcess):
                candidate = psutil.Process(identity["pid"])
                assert candidate.create_time() == identity["created"]
                descendant = candidate
        if descendant is not None:
            with contextlib.suppress(psutil.NoSuchProcess):
                try:
                    descendant.wait(timeout=5.0)
                except psutil.TimeoutExpired:
                    descendant.kill()
                    descendant.wait(timeout=5.0)
