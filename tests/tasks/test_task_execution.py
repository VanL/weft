"""Task execution tests covering reservation flow and control handling.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.2.1], [CC-2.5]
- docs/specifications/07-System_Invariants.md [QUEUE.7], [IMPL.10]
"""

from __future__ import annotations

import base64
import itertools
import json
import os
import queue
import signal
import stat
import sys
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from simplebroker import Queue
from tests.helpers.queue_payloads import terminal_envelopes
from tests.tasks import (
    sample_targets as targets,  # noqa: F401 - ensure module importable
)
from weft._constants import (
    CONTROL_KILL,
    CONTROL_PING,
    CONTROL_STOP,
    PARENT_LOSS_WAKE_INTERVAL_CEILING,
    PIPELINE_OWNER_METADATA_KEY,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    QUEUE_RESERVED_SUFFIX,
    TASKSPEC_TID_SHORT_LENGTH,
    WEFT_ENDPOINTS_REGISTRY_QUEUE,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_STREAMING_SESSIONS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)
from weft.core import launcher as launcher_module
from weft.core.launcher import _request_parent_loss_shutdown, _task_process_entry
from weft.core.manager import Manager
from weft.core.monitor.task_monitor import TaskMonitor
from weft.core.runners import RunnerOutcome
from weft.core.tasks import Consumer
from weft.core.tasks import base as base_module
from weft.core.tasks import consumer as consumer_module
from weft.core.tasks import sessions as sessions_module
from weft.core.tasks.base import BaseTask, TaskWorkerResult
from weft.core.tasks.heartbeat import HeartbeatTask
from weft.core.tasks.monitor import Monitor
from weft.core.tasks.multiqueue_watcher import QueueMessageContext
from weft.core.tasks.pipeline import PipelineEdgeTask, PipelineTask
from weft.core.tasks.service import ServiceTask
from weft.core.tasks.sessions import AgentSession, CommandSession
from weft.core.taskspec import (
    IOSection,
    ReservedPolicy,
    SpecSection,
    StateSection,
    TaskSpec,
)

PROCESS_SCRIPT = str((Path(__file__).resolve().parent / "process_target.py").resolve())
_launcher_wait_calls: list[float | None] = []
_launcher_process_calls = 0
_launcher_run_calls = 0


class LauncherWaitTask:
    def __init__(
        self,
        _db_path: object,
        taskspec: TaskSpec,
        *,
        config: dict[str, object] | None = None,
    ) -> None:
        del config
        self.taskspec = taskspec
        self.should_stop = False
        self.process_calls = 0
        self.taskspec.mark_started(pid=0)
        self.taskspec.mark_running(pid=0)

    def process_once(self) -> None:
        global _launcher_process_calls
        self.process_calls += 1
        _launcher_process_calls = self.process_calls
        if self.process_calls >= 2:
            self.taskspec.mark_completed(return_code=0)

    def wait_for_activity(self, timeout: float | None) -> None:
        _launcher_wait_calls.append(timeout)

    def stop(self, *, join: bool = True) -> None:
        del join
        self.should_stop = True

    def run_until_stopped(self, *, poll_interval: float) -> None:
        global _launcher_process_calls, _launcher_run_calls
        _launcher_run_calls += 1
        _launcher_process_calls = 2
        _launcher_wait_calls.append(poll_interval)


class LauncherTerminalTask(LauncherWaitTask):
    def process_once(self) -> None:
        global _launcher_process_calls
        self.process_calls += 1
        _launcher_process_calls = self.process_calls
        self.taskspec.mark_completed(return_code=0)

    def run_until_stopped(self, *, poll_interval: float) -> None:
        global _launcher_process_calls, _launcher_run_calls
        del poll_interval
        _launcher_run_calls += 1
        _launcher_process_calls = 1


class ReactorTestTask(BaseTask):
    """Small concrete task for BaseTask reactor worker tests."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.worker_results: list[TaskWorkerResult] = []
        self.worker_result_thread_ids: list[int] = []
        self.reactor_turn_count = 0
        self.handled_messages: list[str] = []
        self.handled_message_event = threading.Event()
        self.worker_result_event = threading.Event()
        super().__init__(*args, **kwargs)

    def _process_reactor_turn(self) -> None:
        self.reactor_turn_count += 1
        super()._process_reactor_turn()

    def _build_queue_configs(self) -> dict[str, dict[str, Any]]:
        return {
            self._queue_names["inbox"]: self._read_queue_config(
                self._handle_work_message
            ),
        }

    def _handle_work_message(
        self,
        message: str,
        timestamp: int,
        context: QueueMessageContext,
    ) -> None:
        del timestamp, context
        self.handled_messages.append(message)
        self.handled_message_event.set()

    def _handle_worker_result(self, result: TaskWorkerResult) -> None:
        if result.error is not None:
            super()._handle_worker_result(result)
        self.worker_results.append(result)
        self.worker_result_thread_ids.append(threading.get_ident())
        self.worker_result_event.set()


class ErrorRecordingReactorTestTask(ReactorTestTask):
    """Concrete task that records worker errors instead of raising them."""

    def _handle_worker_result(self, result: TaskWorkerResult) -> None:
        self.worker_results.append(result)
        self.worker_result_thread_ids.append(threading.get_ident())


def test_base_task_process_once_rejects_a_second_drive_thread_before_policy(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    task = ReactorTestTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )
    task.process_once()
    assert task.reactor_turn_count == 1

    errors: list[BaseException] = []

    def drive_from_foreign_thread() -> None:
        try:
            task.process_once()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=drive_from_foreign_thread)
    thread.start()
    thread.join(timeout=2.0)

    try:
        assert not thread.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], RuntimeError)
        assert "reactor owner" in str(errors[0]).lower()
        assert task.reactor_turn_count == 1
    finally:
        task.cleanup()


def test_base_task_simultaneous_process_callers_choose_one_owner(
    broker_env,
    unique_tid: str,
) -> None:
    barrier = threading.Barrier(3)
    turn_entered = threading.Event()
    release_turn = threading.Event()
    outcomes: list[str] = []

    class BlockingOwnerTask(ReactorTestTask):
        def _process_reactor_turn(self) -> None:
            turn_entered.set()
            assert release_turn.wait(timeout=3.0)

    db_path, _make_queue = broker_env
    task = BlockingOwnerTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )

    def drive() -> None:
        barrier.wait(timeout=2.0)
        try:
            task.process_once()
        except RuntimeError:
            outcomes.append("rejected")
        else:
            outcomes.append("owner")

    threads = [threading.Thread(target=drive) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2.0)
    assert turn_entered.wait(timeout=2.0)
    release_turn.set()
    for thread in threads:
        thread.join(timeout=2.0)

    try:
        assert sorted(outcomes) == ["owner", "rejected"]
    finally:
        task.cleanup()


def test_base_task_process_entry_publishes_turn_active_with_owner_claim(
    broker_env,
    unique_tid: str,
    thread_exception_guard: list[threading.ExceptHookArgs],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_claim = threading.Event()
    release_claim = threading.Event()
    turn_entered = threading.Event()
    release_turn = threading.Event()
    stop_started = threading.Event()
    stop_returned = threading.Event()

    class BlockingTurnTask(ReactorTestTask):
        def _block_turn(self) -> None:
            turn_entered.set()
            assert release_turn.wait(timeout=3.0)

        def _process_reactor_turn(self) -> None:
            self._block_turn()

        def _process_stopping_reactor_turn(self) -> None:
            self._block_turn()

    db_path, _make_queue = broker_env
    task = BlockingTurnTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )
    original_claim = task._claim_or_verify_drive_owner_locked

    def pause_after_claim(current: threading.Thread) -> None:
        original_claim(current)
        post_claim.set()
        assert release_claim.wait(timeout=3.0)

    monkeypatch.setattr(
        task,
        "_claim_or_verify_drive_owner_locked",
        pause_after_claim,
    )

    drive_thread = threading.Thread(target=task.process_once)
    drive_thread.start()
    assert post_claim.wait(timeout=2.0)

    def stop_task() -> None:
        stop_started.set()
        task.stop()
        stop_returned.set()

    stop_thread = threading.Thread(target=stop_task)
    stop_thread.start()
    assert stop_started.wait(timeout=2.0)
    assert not stop_returned.wait(timeout=0.05)
    assert task._task_lifecycle.value != "closed"
    assert task._queue_cache

    release_claim.set()
    assert turn_entered.wait(timeout=2.0)
    assert not stop_returned.is_set()
    assert task._task_lifecycle.value != "closed"
    release_turn.set()

    drive_thread.join(timeout=3.0)
    stop_thread.join(timeout=3.0)
    assert not drive_thread.is_alive()
    assert not stop_thread.is_alive()
    assert stop_returned.is_set()
    assert task._task_lifecycle.value == "closed"
    assert task._queue_cache == {}


def test_base_task_run_loop_entry_publishes_active_with_owner_claim(
    broker_env,
    unique_tid: str,
    thread_exception_guard: list[threading.ExceptHookArgs],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_claim = threading.Event()
    release_claim = threading.Event()
    loop_entered = threading.Event()
    release_loop = threading.Event()
    stop_started = threading.Event()
    stop_returned = threading.Event()

    db_path, _make_queue = broker_env
    task = ReactorTestTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )
    original_claim = task._claim_or_verify_drive_owner_locked
    original_pending = task._has_pending_termination_request

    def pause_after_claim(current: threading.Thread) -> None:
        original_claim(current)
        post_claim.set()
        assert release_claim.wait(timeout=3.0)

    def pause_in_loop() -> bool:
        loop_entered.set()
        assert release_loop.wait(timeout=3.0)
        return original_pending()

    monkeypatch.setattr(
        task,
        "_claim_or_verify_drive_owner_locked",
        pause_after_claim,
    )
    monkeypatch.setattr(task, "_has_pending_termination_request", pause_in_loop)

    drive_thread = threading.Thread(target=task.run_until_stopped)
    drive_thread.start()
    assert post_claim.wait(timeout=2.0)

    def stop_task() -> None:
        stop_started.set()
        task.stop()
        stop_returned.set()

    stop_thread = threading.Thread(target=stop_task)
    stop_thread.start()
    assert stop_started.wait(timeout=2.0)
    assert not stop_returned.wait(timeout=0.05)
    assert task._task_lifecycle.value != "closed"
    assert task._queue_cache

    release_claim.set()
    assert loop_entered.wait(timeout=2.0)
    assert not stop_returned.is_set()
    assert task._task_lifecycle.value != "closed"
    release_loop.set()

    drive_thread.join(timeout=3.0)
    stop_thread.join(timeout=3.0)
    assert not drive_thread.is_alive()
    assert not stop_thread.is_alive()
    assert stop_returned.is_set()
    assert task._task_lifecycle.value == "closed"
    assert task._queue_cache == {}


def test_base_task_rejects_reentrant_same_owner_turn(
    broker_env,
    unique_tid: str,
) -> None:
    nested_errors: list[BaseException] = []

    class ReentrantTurnTask(ReactorTestTask):
        def _process_reactor_turn(self) -> None:
            try:
                self.process_once()
            except BaseException as exc:
                nested_errors.append(exc)

    db_path, _make_queue = broker_env
    task = ReentrantTurnTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )
    try:
        task.process_once()

        assert len(nested_errors) == 1
        assert isinstance(nested_errors[0], RuntimeError)
        assert "reentrant" in str(nested_errors[0]).lower()
        assert task._turn_active is False
    finally:
        task.cleanup()


def test_base_task_foreign_wait_rejects_before_waiter_effects(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    task = ReactorTestTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )
    task.process_once()
    ensure_calls = 0

    class ImmediateWaiter:
        def wait(self, timeout: float | None) -> bool:
            del timeout
            return False

    def ensure_waiter() -> ImmediateWaiter:
        nonlocal ensure_calls
        assert task._topology_manual_wait_thread is None
        ensure_calls += 1
        return ImmediateWaiter()

    monkeypatch.setattr(task, "_ensure_multi_activity_waiter", ensure_waiter)
    errors: list[BaseException] = []

    def foreign_wait() -> None:
        try:
            task.wait_for_activity(timeout=0.01)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=foreign_wait)
    thread.start()
    thread.join(timeout=2.0)

    try:
        assert len(errors) == 1
        assert isinstance(errors[0], RuntimeError)
        assert ensure_calls == 0
        task.wait_for_activity(timeout=0.01)
        assert ensure_calls == 1
        assert task._topology_manual_wait_thread is None
    finally:
        task.cleanup()


def test_base_task_foreign_stop_finalizes_idle_manual_owner(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    task = ReactorTestTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )
    task.process_once()
    stop_thread = threading.Thread(target=task.stop)
    stop_thread.start()
    stop_thread.join(timeout=2.0)

    assert not stop_thread.is_alive()
    assert task._queue_cache == {}
    assert task._task_lifecycle.value == "closed"
    with pytest.raises(RuntimeError, match="closed"):
        task.process_once()


def test_base_task_foreign_stop_defers_cleanup_during_owned_standalone_wait(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    task = ReactorTestTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )
    wait_entered = threading.Event()
    release_wait = threading.Event()

    def blocking_wait(_timeout: float | None) -> None:
        wait_entered.set()
        assert release_wait.wait(timeout=2.0)

    task._wait_for_reactor_activity = blocking_wait  # type: ignore[method-assign]

    def drive_and_wait() -> None:
        task.process_once()
        task.wait_for_activity(timeout=1.0)

    owner_thread = threading.Thread(target=drive_and_wait)
    owner_thread.start()
    assert wait_entered.wait(timeout=2.0)

    task.stop(join=False)

    assert task._queue_cache
    assert task._task_lifecycle.value == "stop_requested"

    release_wait.set()
    owner_thread.join(timeout=2.0)

    assert not owner_thread.is_alive()
    assert task._queue_cache == {}
    assert task._task_lifecycle.value == "closed"


def test_base_task_rejects_public_process_once_override() -> None:
    with pytest.raises(TypeError, match="process_once"):

        class InvalidProcessOverride(ReactorTestTask):
            def process_once(self) -> None:
                return


def test_base_task_rejects_public_template_override_in_left_hand_mixin() -> None:
    class ProcessOverrideMixin:
        def process_once(self) -> None:
            return

    with pytest.raises(TypeError, match="process_once"):

        class InvalidMixinOverride(ProcessOverrideMixin, ReactorTestTask):
            pass


@pytest.mark.parametrize("method_name", ["wait_for_activity", "stop", "cleanup"])
def test_base_task_rejects_other_public_template_overrides(method_name: str) -> None:
    namespace = {method_name: lambda self, *args, **kwargs: None}
    with pytest.raises(TypeError, match=method_name):
        type("InvalidPublicTemplateOverride", (ReactorTestTask,), namespace)


def test_base_task_wait_for_activity_requires_an_established_owner(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    task = ReactorTestTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )
    try:
        with pytest.raises(RuntimeError, match="reactor owner"):
            task.wait_for_activity(timeout=0.01)
    finally:
        task.cleanup()


def test_base_task_run_until_stopped_finalizes_on_iteration_limit(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    task = ReactorTestTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )

    task.run_until_stopped(poll_interval=0.0, max_iterations=1)

    assert task.reactor_turn_count == 1
    assert task._queue_cache == {}
    assert task.is_running() is False
    task.stop()


def test_stopping_turn_policy_is_shared_by_all_concrete_task_families() -> None:
    """Every production task family inherits BaseTask's stopping turn [IMPL.10].

    Plan Phase 3 red test 7 (plan section 13.1 item R-9): Consumer carries the
    behavioral stop-with-pending-work coverage; this structural check makes
    the shared-policy assumption explicit. A future per-family override of
    ``_process_stopping_reactor_turn`` fails here until it brings its own
    behavioral stopping coverage.
    """

    families = (
        Consumer,
        ServiceTask,
        HeartbeatTask,
        PipelineTask,
        PipelineEdgeTask,
        Monitor,
        Manager,
        TaskMonitor,
    )
    for family in families:
        assert (
            family._process_stopping_reactor_turn
            is BaseTask._process_stopping_reactor_turn
        ), (
            f"{family.__name__} overrides the stopping turn without "
            "per-family behavioral stopping coverage"
        )


def test_base_task_repeated_stop_does_not_duplicate_cleanup(
    broker_env,
    unique_tid: str,
) -> None:
    """A second stop()/cleanup() re-runs no cleanup phase [IMPL.10].

    Fires plan Phase 4 red test 6 (plan section 13.1 item R-8): repeated stop
    must not re-open or re-close task queues and must not duplicate subtype,
    base, or endpoint/control cleanup work.
    """

    subtype_cleanups = 0
    base_cleanups = 0

    class CountingCleanupTask(ReactorTestTask):
        def _cleanup_task_resources(self, deadline: float) -> None:
            nonlocal subtype_cleanups
            subtype_cleanups += 1
            super()._cleanup_task_resources(deadline)

    db_path, _make_queue = broker_env
    task = CountingCleanupTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )
    original_base_cleanup = task._cleanup_base_task_resources

    def counting_base_cleanup(deadline: float) -> None:
        nonlocal base_cleanups
        base_cleanups += 1
        original_base_cleanup(deadline)

    object.__setattr__(task, "_cleanup_base_task_resources", counting_base_cleanup)

    task.process_once()
    task.stop()

    assert task._task_lifecycle.value == "closed"
    assert subtype_cleanups == 1
    assert base_cleanups == 1
    assert task._queue_cache == {}

    task.stop()
    task.cleanup()

    assert task._task_lifecycle.value == "closed"
    assert subtype_cleanups == 1, "second stop re-ran subtype cleanup"
    assert base_cleanups == 1, "second stop re-ran base queue/waiter cleanup"
    assert task._queue_cache == {}, "second stop re-opened task queues"


def test_base_task_run_until_stopped_finalizes_when_turn_raises(
    broker_env,
    unique_tid: str,
) -> None:
    class FailingTurnTask(ReactorTestTask):
        def _process_reactor_turn(self) -> None:
            raise ValueError("turn failed")

    db_path, _make_queue = broker_env
    task = FailingTurnTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )

    with pytest.raises(ValueError, match="turn failed"):
        task.run_until_stopped(poll_interval=0.0)

    assert task._queue_cache == {}
    assert task.is_running() is False


@pytest.mark.parametrize("initial_exit", ["terminal", "stop_event", "max_zero"])
def test_base_task_run_loop_skips_turn_for_initial_exit_state(
    broker_env,
    unique_tid: str,
    initial_exit: str,
) -> None:
    db_path, _make_queue = broker_env
    task = ReactorTestTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )
    max_iterations: int | None = None
    if initial_exit == "terminal":
        task.taskspec.mark_cancelled(reason="pre-terminal")
    elif initial_exit == "stop_event":
        task._stop_event.set()
    else:
        max_iterations = 0

    task.run_until_stopped(
        poll_interval=0.0,
        max_iterations=max_iterations,
    )

    assert task.reactor_turn_count == 0
    assert task._task_lifecycle.value == "closed"
    assert task._queue_cache == {}


def test_base_task_stop_join_false_defers_cleanup_to_active_driver(
    broker_env,
    unique_tid: str,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingTurnTask(ReactorTestTask):
        def _process_reactor_turn(self) -> None:
            entered.set()
            assert release.wait(timeout=3.0)

    db_path, _make_queue = broker_env
    task = BlockingTurnTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )
    drive_thread = threading.Thread(
        target=lambda: task.run_until_stopped(max_iterations=1),
    )
    drive_thread.start()
    assert entered.wait(timeout=2.0)

    task.stop(join=False)
    assert task._queue_cache

    release.set()
    drive_thread.join(timeout=3.0)
    assert not drive_thread.is_alive()
    assert task._queue_cache == {}


def test_base_task_stop_timeout_leaves_resources_for_driver_finally(
    broker_env,
    unique_tid: str,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingTurnTask(ReactorTestTask):
        def _process_reactor_turn(self) -> None:
            entered.set()
            assert release.wait(timeout=3.0)

    db_path, _make_queue = broker_env
    task = BlockingTurnTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )
    drive_thread = threading.Thread(
        target=lambda: task.run_until_stopped(max_iterations=1),
    )
    drive_thread.start()
    assert entered.wait(timeout=2.0)

    started_at = time.monotonic()
    task.stop(timeout=0.05)
    assert time.monotonic() - started_at < 0.5
    assert task._queue_cache

    release.set()
    drive_thread.join(timeout=3.0)
    assert not drive_thread.is_alive()
    assert task._queue_cache == {}


def test_base_task_cleanup_error_does_not_skip_shared_finalization(
    broker_env,
    unique_tid: str,
) -> None:
    cleanup_calls = 0
    cleanup_running_states: list[bool] = []

    class FailingCleanupTask(ReactorTestTask):
        def _cleanup_task_resources(self, deadline: float) -> None:
            nonlocal cleanup_calls
            del deadline
            cleanup_calls += 1
            cleanup_running_states.append(self.is_running())
            raise ValueError("cleanup failed")

    db_path, _make_queue = broker_env
    task = FailingCleanupTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )

    task.run_until_stopped(poll_interval=0.0, max_iterations=1)
    task.stop()

    assert cleanup_calls == 1
    assert cleanup_running_states == [True]
    assert task._queue_cache == {}
    assert len(task._cleanup_errors) == 1
    assert isinstance(task._cleanup_errors[0], ValueError)


def test_base_task_finalizer_runs_subtype_cleanup_before_base_cleanup(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_order: list[str] = []

    class OrderedCleanupTask(ReactorTestTask):
        def _cleanup_task_resources(self, deadline: float) -> None:
            del deadline
            cleanup_order.append("task")

    db_path, _make_queue = broker_env
    task = OrderedCleanupTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )
    original_base_cleanup = task._cleanup_base_task_resources

    monkeypatch.setattr(
        task,
        "_reset_multi_activity_waiter",
        lambda: cleanup_order.append("waiter"),
    )
    monkeypatch.setattr(
        task._strategy,
        "close",
        lambda: cleanup_order.append("strategy"),
    )

    def record_base_cleanup(deadline: float) -> None:
        cleanup_order.append("base")
        original_base_cleanup(deadline)

    monkeypatch.setattr(task, "_cleanup_base_task_resources", record_base_cleanup)

    task.run_until_stopped(poll_interval=0.0, max_iterations=0)

    assert cleanup_order == ["task", "base", "waiter", "strategy"]


def test_base_task_background_start_uses_task_loop_and_wakes_for_input(
    broker_env,
    unique_tid: str,
    thread_exception_guard: list[threading.ExceptHookArgs],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = ReactorTestTask(db_path, spec)
    entered_real_wait = threading.Event()
    real_wait = task._wait_for_reactor_activity

    def entered_wait(timeout: float | None) -> None:
        entered_real_wait.set()
        real_wait(timeout)

    monkeypatch.setattr(task, "_wait_for_reactor_activity", entered_wait)
    thread = task.start()
    assert entered_real_wait.wait(timeout=2.0)
    assert task.is_running()

    make_queue(spec.io.inputs["inbox"]).write("wake")
    assert task.handled_message_event.wait(timeout=2.0)

    task.stop()
    thread.join(timeout=2.0)
    assert task.handled_messages == ["wake"]
    assert task._queue_cache == {}
    assert task.is_running() is False


def test_base_task_background_start_rejects_existing_manual_owner(
    broker_env,
    unique_tid: str,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    db_path, _make_queue = broker_env
    task = ReactorTestTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )
    task.process_once()

    try:
        with pytest.raises(RuntimeError, match="cannot start|reactor owner"):
            task.start()
    finally:
        task.cleanup()


def test_base_task_worker_result_wakes_background_reactor_after_real_wait(
    broker_env,
    unique_tid: str,
    thread_exception_guard: list[threading.ExceptHookArgs],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    task = ReactorTestTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )
    release_worker = threading.Event()
    worker_started = threading.Event()

    def worker() -> str:
        worker_started.set()
        assert release_worker.wait(timeout=3.0)
        return "done"

    task._submit_worker_call("blocked", worker)
    assert worker_started.wait(timeout=2.0)
    monkeypatch.setattr(task, "next_wait_timeout", lambda: 5.0)

    entered_real_wait = threading.Event()
    real_wait = task._wait_for_reactor_activity

    def entered_wait(timeout: float | None) -> None:
        entered_real_wait.set()
        real_wait(timeout)

    monkeypatch.setattr(task, "_wait_for_reactor_activity", entered_wait)
    thread = task.start()
    assert entered_real_wait.wait(timeout=2.0)

    release_worker.set()
    assert task.worker_result_event.wait(timeout=1.0)

    task.stop()
    thread.join(timeout=2.0)
    assert [result.value for result in task.worker_results] == ["done"]


def test_base_task_background_start_failure_rolls_back_owner_state(
    broker_env,
    unique_tid: str,
    thread_exception_guard: list[threading.ExceptHookArgs],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    task = ReactorTestTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )

    def fail_start(_thread: threading.Thread) -> None:
        raise RuntimeError("thread start failed")

    monkeypatch.setattr(base_module.threading.Thread, "start", fail_start)
    with pytest.raises(RuntimeError, match="thread start failed"):
        task.start()

    assert task._task_lifecycle.value == "new"
    assert task._drive_owner_thread is None
    assert task._start_pending is False
    task.cleanup()


def test_base_task_stop_waits_for_starting_interlock(
    broker_env,
    unique_tid: str,
    thread_exception_guard: list[threading.ExceptHookArgs],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    task = ReactorTestTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )
    start_entered = threading.Event()
    release_start = threading.Event()
    stop_returned = threading.Event()
    start_errors: list[BaseException] = []
    original_thread_start = threading.Thread.start

    def blocked_thread_start(thread: threading.Thread) -> None:
        start_entered.set()
        assert release_start.wait(timeout=3.0)
        original_thread_start(thread)

    def start_task() -> None:
        monkeypatch.setattr(threading.Thread, "start", blocked_thread_start)
        try:
            task.start()
        except BaseException as exc:
            start_errors.append(exc)

    def stop_task() -> None:
        task.stop()
        stop_returned.set()

    starter = threading.Thread(target=start_task)
    stopper = threading.Thread(target=stop_task)
    original_thread_start(starter)
    assert start_entered.wait(timeout=2.0)
    assert task._task_lifecycle.value == "starting"
    assert task._start_pending is True

    original_thread_start(stopper)
    assert stop_returned.wait(timeout=0.05) is False

    release_start.set()
    starter.join(timeout=3.0)
    stopper.join(timeout=3.0)

    assert start_errors == []
    assert stop_returned.is_set()
    assert task._task_lifecycle.value == "closed"
    assert task._queue_cache == {}


def test_base_task_owner_stop_inside_turn_finalizes_after_unwind(
    broker_env,
    unique_tid: str,
) -> None:
    cleanup_observations: list[bool] = []

    class SelfStoppingTask(ReactorTestTask):
        def _process_reactor_turn(self) -> None:
            self.stop()
            assert self._queue_cache

        def _cleanup_task_resources(self, deadline: float) -> None:
            del deadline
            cleanup_observations.append(self.is_running())

    db_path, _make_queue = broker_env
    task = SelfStoppingTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )

    task.process_once()

    assert task._queue_cache == {}
    assert cleanup_observations == [False]
    assert task._task_lifecycle.value == "closed"


def test_base_task_strategy_and_finalizer_close_once(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    task = ReactorTestTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )
    starts = 0
    closes = 0
    original_start = task._start_strategy
    original_close = task._strategy.close

    def record_start() -> None:
        nonlocal starts
        starts += 1
        original_start()

    def record_close() -> None:
        nonlocal closes
        closes += 1
        original_close()

    monkeypatch.setattr(task, "_start_strategy", record_start)
    monkeypatch.setattr(task._strategy, "close", record_close)

    task.run_until_stopped(poll_interval=0.0, max_iterations=2)
    task.stop()

    assert starts == 1
    assert closes == 1
    assert task._finalizer.alive is False


def test_base_task_cleanup_covers_primary_watcher_queue_handle(
    broker_env,
    unique_tid: str,
) -> None:
    class AuxiliaryWatchedTask(ReactorTestTask):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.auxiliary_queue_name = f"auxiliary.{unique_tid}"
            super().__init__(*args, **kwargs)

        def _reactor_queue_roles(self) -> dict[str, str]:
            roles = super()._reactor_queue_roles()
            roles["auxiliary"] = self.auxiliary_queue_name
            return roles

        def _build_queue_configs(self) -> dict[str, dict[str, Any]]:
            configs = super()._build_queue_configs()
            configs[self.auxiliary_queue_name] = self._read_queue_config(
                self._handle_work_message
            )
            return configs

    db_path, _make_queue = broker_env
    task = AuxiliaryWatchedTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )
    primary_queue = task._queue_obj
    assert any(primary_queue is queue for queue in task._queue_cache.values())

    task.cleanup()

    assert primary_queue._finalizer.alive is False


def test_consumer_cleanup_propagates_one_deadline_to_owned_sessions(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    task = Consumer(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )
    observed_deadlines: list[float] = []

    class FakeAgentSession:
        def close(self, *, deadline: float | None = None) -> None:
            assert deadline is not None
            observed_deadlines.append(deadline)

    task._interactive_mode = True
    task._agent_session = FakeAgentSession()  # type: ignore[assignment]
    monkeypatch.setattr(
        task,
        "_interactive_shutdown",
        lambda *, deadline, reason=None: observed_deadlines.append(deadline),
    )

    started_at = time.monotonic()
    task.stop(timeout=0.2)

    assert len(observed_deadlines) == 2
    assert observed_deadlines[0] == observed_deadlines[1]
    assert started_at < observed_deadlines[0] <= started_at + 0.25


def test_agent_session_close_caps_join_to_caller_deadline() -> None:
    join_timeouts: list[float] = []

    class FakeProcess:
        pid = None

        def __init__(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float | None = None) -> None:
            join_timeouts.append(float(timeout or 0.0))

        def kill(self) -> None:
            self.alive = False

        def close(self) -> None:
            return

    class FakeQueue:
        def put(self, _value: object) -> None:
            return

        def close(self) -> None:
            return

        def join_thread(self) -> None:
            return

    process = FakeProcess()
    session = AgentSession(
        process,  # type: ignore[arg-type]
        FakeQueue(),  # type: ignore[arg-type]
        FakeQueue(),  # type: ignore[arg-type]
        None,
        None,
        timeout=None,
    )
    started_at = time.monotonic()
    session.close(deadline=started_at + 0.05)

    assert join_timeouts
    assert 0.0 <= join_timeouts[0] <= 0.05
    assert process.alive is False


def test_agent_session_deadline_close_does_not_join_ipc_feeder_threads() -> None:
    class FakeProcess:
        pid = None

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            del timeout

        def close(self) -> None:
            return

    class DeadlineQueue:
        def __init__(self) -> None:
            self.cancel_calls = 0
            self.join_calls = 0

        def cancel_join_thread(self) -> None:
            self.cancel_calls += 1

        def close(self) -> None:
            return

        def join_thread(self) -> None:
            self.join_calls += 1

    request_queue = DeadlineQueue()
    response_queue = DeadlineQueue()
    session = AgentSession(
        FakeProcess(),  # type: ignore[arg-type]
        request_queue,  # type: ignore[arg-type]
        response_queue,  # type: ignore[arg-type]
        None,
        None,
        timeout=None,
    )

    session.close(deadline=time.monotonic())

    assert request_queue.cancel_calls == 1
    assert response_queue.cancel_calls == 1
    assert request_queue.join_calls == 0
    assert response_queue.join_calls == 0


def test_agent_session_deadline_preserves_process_tree_kill_escalation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcess:
        pid = 456

        def __init__(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def kill(self) -> None:
            self.alive = False

    class FakeQueue:
        pass

    process = FakeProcess()
    calls: list[tuple[int, float, bool]] = []

    def terminate_tree(
        pid: int,
        *,
        timeout: float,
        kill_after: bool,
    ) -> set[int]:
        calls.append((pid, timeout, kill_after))
        process.alive = False
        return {pid}

    monkeypatch.setattr(sessions_module, "terminate_process_tree", terminate_tree)
    session = AgentSession(
        process,  # type: ignore[arg-type]
        FakeQueue(),  # type: ignore[arg-type]
        FakeQueue(),  # type: ignore[arg-type]
        None,
        None,
        timeout=None,
    )

    session.terminate(deadline=time.monotonic() + 0.2)

    assert len(calls) == 1
    pid, timeout, kill_after = calls[0]
    assert pid == 456
    assert 0.0 < timeout <= 0.1
    assert kill_after is True


def test_agent_session_expired_deadline_uses_nonblocking_hard_tree_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcess:
        pid = 654

        def __init__(self) -> None:
            self.alive = True
            self.kill_calls = 0

        def is_alive(self) -> bool:
            return self.alive

        def kill(self) -> None:
            self.kill_calls += 1
            self.alive = False

    class FakeQueue:
        pass

    process = FakeProcess()
    calls: list[tuple[int, float, bool]] = []

    def terminate_tree(
        pid: int,
        *,
        timeout: float,
        kill_after: bool,
    ) -> set[int]:
        calls.append((pid, timeout, kill_after))
        return set()

    monkeypatch.setattr(sessions_module, "terminate_process_tree", terminate_tree)
    session = AgentSession(
        process,  # type: ignore[arg-type]
        FakeQueue(),  # type: ignore[arg-type]
        FakeQueue(),  # type: ignore[arg-type]
        None,
        None,
        timeout=None,
    )

    session.terminate(deadline=time.monotonic() - 1.0)

    assert calls == [(654, 0.0, True)]
    assert process.kill_calls == 1


def test_command_session_expired_cleanup_deadline_does_not_start_fresh_wait() -> None:
    class FakeProcess:
        pid = None

        def __init__(self) -> None:
            self.alive = True
            self.kill_calls = 0

        def poll(self) -> int | None:
            return None if self.alive else -9

        def kill(self) -> None:
            self.kill_calls += 1
            self.alive = False

        def wait(self, timeout: float | None = None) -> int:
            raise AssertionError(f"fresh wait started with timeout={timeout}")

        def terminate(self) -> None:
            raise AssertionError("fresh graceful termination started after deadline")

    process = FakeProcess()
    session = CommandSession(
        process,  # type: ignore[arg-type]
        queue.Queue(),
        queue.Queue(),
        None,
        None,
    )

    session.terminate(deadline=time.monotonic())

    assert process.kill_calls == 1
    assert process.alive is False


def test_command_session_deadline_preserves_process_tree_kill_escalation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcess:
        pid = 123

        def __init__(self) -> None:
            self.alive = True

        def poll(self) -> int | None:
            return None if self.alive else -9

        def kill(self) -> None:
            self.alive = False

    process = FakeProcess()
    calls: list[tuple[int, float, bool]] = []

    def terminate_tree(
        pid: int,
        *,
        timeout: float,
        kill_after: bool,
    ) -> set[int]:
        calls.append((pid, timeout, kill_after))
        process.alive = False
        return {pid}

    monkeypatch.setattr(sessions_module, "terminate_process_tree", terminate_tree)
    session = CommandSession(
        process,  # type: ignore[arg-type]
        queue.Queue(),
        queue.Queue(),
        None,
        None,
    )

    session.terminate(deadline=time.monotonic() + 0.2)

    assert len(calls) == 1
    pid, timeout, kill_after = calls[0]
    assert pid == 123
    assert 0.0 < timeout <= 0.1
    assert kill_after is True


def test_command_session_expired_deadline_uses_nonblocking_hard_tree_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcess:
        pid = 789

        def __init__(self) -> None:
            self.alive = True
            self.kill_calls = 0

        def poll(self) -> int | None:
            return None if self.alive else -9

        def kill(self) -> None:
            self.kill_calls += 1
            self.alive = False

    process = FakeProcess()
    calls: list[tuple[int, float, bool]] = []

    def terminate_tree(
        pid: int,
        *,
        timeout: float,
        kill_after: bool,
    ) -> set[int]:
        calls.append((pid, timeout, kill_after))
        return set()

    monkeypatch.setattr(sessions_module, "terminate_process_tree", terminate_tree)
    session = CommandSession(
        process,  # type: ignore[arg-type]
        queue.Queue(),
        queue.Queue(),
        None,
        None,
    )

    session.terminate(deadline=time.monotonic() - 1.0)

    assert calls == [(789, 0.0, True)]
    assert process.kill_calls == 1


def test_base_task_direct_run_forever_sigint_handler_only_records(
    broker_env,
    unique_tid: str,
) -> None:
    handler_observations: list[tuple[bool, tuple[tuple[str, int | None], ...]]] = []

    class SigintTask(ReactorTestTask):
        def _process_reactor_turn(self) -> None:
            self._sigint_handler(signal.SIGINT, None)
            handler_observations.append(
                (
                    self._stop_event.is_set(),
                    tuple(self._pending_termination_sources),
                )
            )

    db_path, _make_queue = broker_env
    task = SigintTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )

    task.run_forever()

    assert handler_observations == [(False, (("signal", signal.SIGINT),))]
    assert task.taskspec.state.status == "cancelled"
    assert task._has_pending_termination_request() is False
    assert task._task_lifecycle.value == "closed"


class ReservedSupportCollisionReactorTestTask(ReactorTestTask):
    """Expose the one derived base role that cannot be configured in TaskSpec."""

    def __init__(
        self,
        *args: Any,
        collision_queue: str,
        **kwargs: Any,
    ) -> None:
        self._collision_queue = collision_queue
        super().__init__(*args, **kwargs)

    def _resolve_queue_names(self) -> dict[str, str]:
        roles = super()._resolve_queue_names()
        roles["reserved"] = self._collision_queue
        return roles


class CanonicalRoleReplacementReactorTestTask(ReactorTestTask):
    """Attempt to replace one canonical semantic role declaration."""

    def __init__(self, *args: Any, replacement_role: str, **kwargs: Any) -> None:
        self._replacement_role = replacement_role
        super().__init__(*args, **kwargs)

    def _reactor_queue_roles(self) -> dict[str, str]:
        roles = super()._reactor_queue_roles()
        roles[self._replacement_role] = "replacement.queue"
        return roles


class CanonicalSupportReplacementReactorTestTask(ReactorTestTask):
    """Attempt to replace one canonical support-route declaration."""

    def __init__(self, *args: Any, replacement_role: str, **kwargs: Any) -> None:
        self._replacement_role = replacement_role
        super().__init__(*args, **kwargs)

    def _reactor_support_routes(self) -> dict[str, str]:
        routes = super()._reactor_support_routes()
        routes[self._replacement_role] = "replacement.queue"
        return routes


class RoleSupportKeyOverlapReactorTestTask(ReactorTestTask):
    """Attempt to reuse one task-role key as a support-route key."""

    def __init__(self, *args: Any, overlap_role: str, **kwargs: Any) -> None:
        self._overlap_role = overlap_role
        super().__init__(*args, **kwargs)

    def _reactor_support_routes(self) -> dict[str, str]:
        routes = super()._reactor_support_routes()
        routes[self._overlap_role] = "overlap.queue"
        return routes


def drain_queue(queue) -> list[str]:
    messages: list[str] = []
    while True:
        value = queue.read_one()
        if value is None:
            break
        messages.append(value)
    return messages


def make_function_taskspec(
    tid: str,
    function_target: str,
    *,
    cleanup_on_exit: bool = False,
    reserved_stop: ReservedPolicy = ReservedPolicy.KEEP,
    reserved_error: ReservedPolicy = ReservedPolicy.KEEP,
    weft_context: str | None = None,
    output_size_limit_mb: float | None = None,
    stream_output: bool | None = None,
) -> TaskSpec:
    """Create a TaskSpec for function execution with explicit queue mappings."""
    return TaskSpec(
        tid=tid,
        name="task-func",
        spec=SpecSection(
            type="function",
            function_target=function_target,
            cleanup_on_exit=cleanup_on_exit,
            reserved_policy_on_stop=reserved_stop,
            reserved_policy_on_error=reserved_error,
            weft_context=weft_context,
            output_size_limit_mb=output_size_limit_mb,
            stream_output=stream_output if stream_output is not None else False,
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.{QUEUE_OUTBOX_SUFFIX}"},
            control={
                "ctrl_in": f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        ),
        state=StateSection(),
    )


def make_command_taskspec(
    tid: str,
    process_target: str,
    *,
    args: list[str] | None = None,
    cleanup_on_exit: bool = False,
    reserved_stop: ReservedPolicy = ReservedPolicy.KEEP,
    reserved_error: ReservedPolicy = ReservedPolicy.KEEP,
    persistent: bool = False,
    stream_output: bool | None = None,
) -> TaskSpec:
    """Create a TaskSpec for command execution with explicit queue mappings."""
    return TaskSpec(
        tid=tid,
        name="task-command",
        spec=SpecSection(
            type="command",
            process_target=process_target,
            args=list(args or []),
            cleanup_on_exit=cleanup_on_exit,
            reserved_policy_on_stop=reserved_stop,
            reserved_policy_on_error=reserved_error,
            persistent=persistent,
            stream_output=stream_output if stream_output is not None else False,
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.{QUEUE_OUTBOX_SUFFIX}"},
            control={
                "ctrl_in": f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        ),
        state=StateSection(),
    )


def with_queue_role_overrides(
    taskspec: TaskSpec,
    **overrides: str,
) -> TaskSpec:
    """Return a validated TaskSpec with selected queue roles replaced."""

    payload = taskspec.model_dump(mode="json")
    io = payload["io"]
    for role, value in overrides.items():
        if role == "inbox":
            io["inputs"]["inbox"] = value
        elif role == "outbox":
            io["outputs"]["outbox"] = value
        else:
            io["control"][role] = value
    return TaskSpec.model_validate(payload)


@pytest.mark.parametrize(
    ("left_role", "right_role"),
    list(
        itertools.combinations(
            ("inbox", "reserved", "outbox", "ctrl_in", "ctrl_out"),
            2,
        )
    ),
)
def test_base_task_rejects_duplicate_queue_roles_before_broker_side_effects(
    tmp_path: Path,
    left_role: str,
    right_role: str,
) -> None:
    """Construction rejects aliases before opening the broker database [QUEUE.7]."""

    tid = "1778089999999999001"
    db_path = tmp_path / f"duplicate-{left_role}-{right_role}.sqlite3"
    spec = make_function_taskspec(
        tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    role_values = {
        "inbox": spec.io.inputs["inbox"],
        "reserved": f"T{tid}.{QUEUE_RESERVED_SUFFIX}",
        "outbox": spec.io.outputs["outbox"],
        "ctrl_in": spec.io.control["ctrl_in"],
        "ctrl_out": spec.io.control["ctrl_out"],
    }
    overrides = (
        {right_role: role_values[left_role]}
        if right_role != "reserved"
        else {left_role: role_values[right_role]}
    )
    spec = with_queue_role_overrides(spec, **overrides)

    with pytest.raises(ValueError) as exc_info:
        ReactorTestTask(db_path, spec)

    message = str(exc_info.value)
    assert left_role in message
    assert right_role in message
    assert role_values[left_role if right_role != "reserved" else right_role] in message
    assert db_path.exists() is False
    tid_short = tid[-TASKSPEC_TID_SHORT_LENGTH:]
    assert not any(tid_short in thread.name for thread in threading.enumerate())


@pytest.mark.parametrize(
    ("support_role", "support_queue"),
    (
        ("global_log", WEFT_GLOBAL_LOG_QUEUE),
        ("tid_mappings", WEFT_TID_MAPPINGS_QUEUE),
        ("streaming_sessions", WEFT_STREAMING_SESSIONS_QUEUE),
        ("endpoints_registry", WEFT_ENDPOINTS_REGISTRY_QUEUE),
    ),
)
@pytest.mark.parametrize(
    "local_role",
    ("inbox", "outbox", "ctrl_in", "ctrl_out"),
)
def test_base_task_rejects_support_route_collisions_before_broker_side_effects(
    tmp_path: Path,
    local_role: str,
    support_role: str,
    support_queue: str,
) -> None:
    """Every BaseTask support route is protected from local role aliases [QUEUE.7]."""

    tid = "1778089999999999002"
    db_path = tmp_path / f"support-{local_role}-{support_role}.sqlite3"
    spec = make_function_taskspec(
        tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    spec = with_queue_role_overrides(spec, **{local_role: support_queue})

    with pytest.raises(ValueError) as exc_info:
        ReactorTestTask(db_path, spec)

    message = str(exc_info.value)
    assert local_role in message
    assert support_role in message
    assert support_queue in message
    assert db_path.exists() is False


@pytest.mark.parametrize(
    ("support_role", "support_queue"),
    (
        ("global_log", WEFT_GLOBAL_LOG_QUEUE),
        ("tid_mappings", WEFT_TID_MAPPINGS_QUEUE),
        ("streaming_sessions", WEFT_STREAMING_SESSIONS_QUEUE),
        ("endpoints_registry", WEFT_ENDPOINTS_REGISTRY_QUEUE),
    ),
)
def test_base_task_rejects_derived_reserved_support_route_collision(
    tmp_path: Path,
    support_role: str,
    support_queue: str,
) -> None:
    """The derived reserved role participates in support validation [QUEUE.7]."""

    tid = "1778089999999999005"
    db_path = tmp_path / f"support-reserved-{support_role}.sqlite3"
    spec = make_function_taskspec(
        tid,
        "tests.tasks.sample_targets:echo_payload",
    )

    with pytest.raises(ValueError) as exc_info:
        ReservedSupportCollisionReactorTestTask(
            db_path,
            spec,
            collision_queue=support_queue,
        )

    message = str(exc_info.value)
    assert "reserved" in message
    assert support_role in message
    assert support_queue in message
    assert db_path.exists() is False


@pytest.mark.parametrize(
    "canonical_role",
    ("inbox", "reserved", "outbox", "ctrl_in", "ctrl_out"),
)
def test_base_task_rejects_canonical_semantic_role_replacement(
    tmp_path: Path,
    canonical_role: str,
) -> None:
    """Subtype declarations cannot hide canonical task roles [QUEUE.7]."""

    tid = "1778089999999999006"
    db_path = tmp_path / f"canonical-role-{canonical_role}.sqlite3"
    spec = make_function_taskspec(
        tid,
        "tests.tasks.sample_targets:echo_payload",
    )

    with pytest.raises(ValueError) as exc_info:
        CanonicalRoleReplacementReactorTestTask(
            db_path,
            spec,
            replacement_role=canonical_role,
        )

    message = str(exc_info.value)
    assert "Canonical queue role" in message
    assert canonical_role in message
    assert db_path.exists() is False


@pytest.mark.parametrize(
    "canonical_support",
    ("global_log", "tid_mappings", "streaming_sessions", "endpoints_registry"),
)
def test_base_task_rejects_canonical_support_route_replacement(
    tmp_path: Path,
    canonical_support: str,
) -> None:
    """Subtype declarations cannot hide canonical support routes [QUEUE.7]."""

    tid = "1778089999999999007"
    db_path = tmp_path / f"canonical-support-{canonical_support}.sqlite3"
    spec = make_function_taskspec(
        tid,
        "tests.tasks.sample_targets:echo_payload",
    )

    with pytest.raises(ValueError) as exc_info:
        CanonicalSupportReplacementReactorTestTask(
            db_path,
            spec,
            replacement_role=canonical_support,
        )

    message = str(exc_info.value)
    assert "Canonical support route" in message
    assert canonical_support in message
    assert db_path.exists() is False


@pytest.mark.parametrize(
    "overlap_role",
    ("inbox", "reserved", "outbox", "ctrl_in", "ctrl_out"),
)
def test_base_task_rejects_role_support_semantic_key_overlap(
    tmp_path: Path,
    overlap_role: str,
) -> None:
    """Role and support maps cannot silently overwrite semantic keys [QUEUE.7]."""

    tid = "1778089999999999008"
    db_path = tmp_path / f"role-support-overlap-{overlap_role}.sqlite3"
    spec = make_function_taskspec(
        tid,
        "tests.tasks.sample_targets:echo_payload",
    )

    with pytest.raises(ValueError) as exc_info:
        RoleSupportKeyOverlapReactorTestTask(
            db_path,
            spec,
            overlap_role=overlap_role,
        )

    message = str(exc_info.value)
    assert "duplicate semantic keys" in message
    assert overlap_role in message
    assert db_path.exists() is False


@pytest.mark.parametrize(
    ("other_role", "queue_name"),
    (
        ("inbox", None),
        ("reserved", None),
        ("outbox", None),
        ("ctrl_in", None),
        ("ctrl_out", None),
        ("global_log", WEFT_GLOBAL_LOG_QUEUE),
        ("tid_mappings", WEFT_TID_MAPPINGS_QUEUE),
        ("streaming_sessions", WEFT_STREAMING_SESSIONS_QUEUE),
        ("endpoints_registry", WEFT_ENDPOINTS_REGISTRY_QUEUE),
    ),
)
def test_pipeline_owned_consumer_rejects_owner_event_route_collision(
    tmp_path: Path,
    other_role: str,
    queue_name: str | None,
) -> None:
    """Pipeline owner events cannot alias Consumer task routes [QUEUE.7]."""

    tid = "1778089999999999009"
    spec = make_function_taskspec(
        tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    role_values = {
        "inbox": spec.io.inputs["inbox"],
        "reserved": f"T{tid}.{QUEUE_RESERVED_SUFFIX}",
        "outbox": spec.io.outputs["outbox"],
        "ctrl_in": spec.io.control["ctrl_in"],
        "ctrl_out": spec.io.control["ctrl_out"],
    }
    duplicate_queue = queue_name or role_values[other_role]
    payload = spec.model_dump(mode="json")
    payload["metadata"][PIPELINE_OWNER_METADATA_KEY] = {
        "pipeline_tid": "1778089999999999900",
        "events_queue": duplicate_queue,
        "role": "pipeline_stage",
        "stage_name": "stage",
    }
    db_path = tmp_path / f"pipeline-owner-{other_role}-alias.sqlite3"

    with pytest.raises(ValueError) as exc_info:
        Consumer(db_path, TaskSpec.model_validate(payload))

    message = str(exc_info.value)
    assert "pipeline_owner_events" in message
    assert other_role in message
    assert duplicate_queue in message
    assert db_path.exists() is False


@pytest.mark.parametrize("events_queue", ("", "   "))
def test_pipeline_owned_consumer_rejects_blank_owner_event_route(
    tmp_path: Path,
    events_queue: str,
) -> None:
    """Pipeline owner event output must be a non-empty fixed route [QUEUE.7]."""

    tid = "1778089999999999010"
    spec = make_function_taskspec(
        tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    payload = spec.model_dump(mode="json")
    payload["metadata"][PIPELINE_OWNER_METADATA_KEY] = {
        "pipeline_tid": "1778089999999999900",
        "events_queue": events_queue,
        "role": "pipeline_stage",
        "stage_name": "stage",
    }
    db_path = tmp_path / f"pipeline-owner-blank-{len(events_queue)}.sqlite3"

    with pytest.raises(ValueError) as exc_info:
        Consumer(db_path, TaskSpec.model_validate(payload))

    message = str(exc_info.value)
    assert "events_queue" in message
    assert "non-empty" in message
    assert db_path.exists() is False


@pytest.mark.parametrize(
    ("role", "queue_name"),
    (
        ("inbox", "   "),
        ("outbox", "\t"),
        ("ctrl_in", ""),
        ("ctrl_in", "   "),
        ("ctrl_out", ""),
        ("ctrl_out", "\n"),
    ),
)
def test_base_task_rejects_empty_resolved_queue_roles_before_broker_side_effects(
    tmp_path: Path,
    role: str,
    queue_name: str,
) -> None:
    """Configurable roles must resolve to non-blank queue names [QUEUE.7]."""

    tid = "1778089999999999004"
    db_path = tmp_path / f"empty-{role}-{len(queue_name)}.sqlite3"
    spec = with_queue_role_overrides(
        make_function_taskspec(tid, "tests.tasks.sample_targets:echo_payload"),
        **{role: queue_name},
    )

    with pytest.raises(ValueError, match=rf"{role!s}.*non-empty"):
        ReactorTestTask(db_path, spec)

    assert db_path.exists() is False


def test_base_task_rejects_runtime_queue_mutation(tmp_path: Path) -> None:
    """Task reactors seal topology while standalone watchers remain dynamic [QUEUE.7]."""

    db_path = tmp_path / "fixed-topology.sqlite3"
    tid = "1778089999999999003"
    task = ReactorTestTask(
        db_path,
        make_function_taskspec(tid, "tests.tasks.sample_targets:echo_payload"),
    )

    try:
        with pytest.raises(RuntimeError, match="topology is fixed.*add_queue"):
            task.add_queue("late.queue", task._handle_work_message)
        with pytest.raises(RuntimeError, match="topology is fixed.*remove_queue"):
            task.remove_queue(task._queue_names["inbox"])
    finally:
        task.cleanup()


def _instrument_streaming_queue(monkeypatch):
    writes: list[dict[str, object]] = []
    deletes: list[int | None] = []
    original_queue = BaseTask._queue
    proxies: dict[int, Queue] = {}

    class QueueProxy:
        def __init__(self, delegate: Queue) -> None:
            self._delegate = delegate

        def write(self, message: str) -> None:
            writes.append(json.loads(message))
            return self._delegate.write(message)

        def delete(self, *args, **kwargs) -> None:
            message_id = kwargs.get("message_id")
            if message_id is None and args:
                message_id = args[0]
            deletes.append(message_id)
            return self._delegate.delete(*args, **kwargs)

        def __getattr__(self, attr: str):
            return getattr(self._delegate, attr)

    def instrument(self, name: str) -> Queue:
        queue = original_queue(self, name)
        if name != WEFT_STREAMING_SESSIONS_QUEUE:
            return queue
        proxy = proxies.get(id(queue))
        if proxy is None:
            proxy = QueueProxy(queue)
            proxies[id(queue)] = proxy
        return proxy

    monkeypatch.setattr(BaseTask, "_queue", instrument, raising=False)
    return writes, deletes


@pytest.fixture
def unique_tid() -> str:
    """Generate a unique-ish TID for tests."""
    import time

    return str(time.time_ns())


def test_task_processes_function_target_and_writes_outbox(
    broker_env, unique_tid: str
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = Consumer(db_path, spec)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    inbox_name = spec.io.inputs["inbox"]
    reserved_name = f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}"
    outbox_name = spec.io.outputs["outbox"]

    inbox = make_queue(inbox_name)
    inbox.write(json.dumps({"args": ["payload"], "kwargs": {"suffix": "!"}}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

    outbox = make_queue(outbox_name)
    result = outbox.read_one()
    assert result == "payload!"

    reserved_queue = make_queue(reserved_name)
    assert reserved_queue.peek_one() is None
    assert task.taskspec.state.status == "completed"
    assert task.taskspec.state.return_code == 0
    events = [json.loads(msg) for msg in drain_queue(log_queue)]
    assert any(event["event"] == "work_completed" for event in events)

    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    envelopes = terminal_envelopes(ctrl_out, tid=unique_tid, source="task")
    assert len(envelopes) == 1
    terminal = envelopes[0]
    assert terminal["status"] == "completed"
    assert terminal["return_code"] == 0


def test_run_work_item_executes_payload(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = Consumer(db_path, spec)

    result = task.run_work_item({"args": ["direct"], "kwargs": {"suffix": "!"}})

    assert result == "direct!"
    outbox = make_queue(spec.io.outputs["outbox"])
    assert outbox.read_one() == "direct!"
    task.cleanup()


def test_run_work_item_spills_large_output(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:large_output",
        output_size_limit_mb=1,
    )
    task = Consumer(db_path, spec)

    size = 2 * 1024 * 1024
    result = task.run_work_item({"kwargs": {"size": size}})

    assert isinstance(result, str)
    assert len(result) == size

    outbox = make_queue(spec.io.outputs["outbox"])
    reference = json.loads(outbox.read_one())
    assert reference["type"] == "large_output"
    task.cleanup()


def test_run_work_item_deferred_stop_without_active_message_preserves_reserved_queue(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
        reserved_stop=ReservedPolicy.CLEAR,
    )
    task = Consumer(db_path, spec)

    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    reserved.write("job")

    def fake_run_task_for_reactor(_work_item: object) -> tuple[RunnerOutcome, bool]:
        return (
            RunnerOutcome(
                status="cancelled",
                value=None,
                error="Target execution cancelled",
                stdout=None,
                stderr=None,
                returncode=None,
                duration=0.0,
            ),
            False,
        )

    monkeypatch.setattr(task, "_run_task_for_reactor", fake_run_task_for_reactor)
    task._defer_active_control(CONTROL_STOP, int(unique_tid) + 1)

    with pytest.raises(RuntimeError, match="STOP command received"):
        task.run_work_item({"args": ["direct"]})

    assert task.taskspec.state.status == "cancelled"
    assert reserved.read_one() == "job"
    assert make_queue(spec.io.control["ctrl_out"]).read_one() is not None
    task.cleanup()


def test_deferred_stop_finalizes_before_timeout_outcome(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        reserved_stop=ReservedPolicy.CLEAR,
    )
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    worker_started = threading.Event()
    release_worker = threading.Event()

    class TimeoutTaskRunner:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def supports_stream_callbacks(self) -> bool:
            return False

        def run_with_hooks(
            self,
            work_item: Any,
            **_kwargs: Any,
        ) -> RunnerOutcome:
            del work_item
            worker_started.set()
            release_worker.wait()
            return RunnerOutcome(
                status="timeout",
                value=None,
                error="runner timed out",
                stdout=None,
                stderr=None,
                returncode=None,
                duration=0.0,
            )

    monkeypatch.setattr(consumer_module, "TaskRunner", TimeoutTaskRunner)
    inbox.write(json.dumps({"args": []}))

    try:
        task.process_once()
        assert worker_started.wait(timeout=2.0)
        assert task.taskspec.state.status == "running"

        ctrl_in.write(CONTROL_STOP)
        task.process_once()
        assert task._deferred_active_control_command == CONTROL_STOP
    finally:
        release_worker.set()

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status in {"cancelled", "timeout"},
    )

    responses = [json.loads(message) for message in drain_queue(ctrl_out)]
    assert any(
        response.get("command") == "STOP" and response["status"] == "ack"
        for response in responses
    )
    assert task.taskspec.state.status == "cancelled"
    assert task._deferred_active_control_command is None
    assert reserved.has_pending() is False
    task.cleanup()


def test_deferred_kill_finalizes_before_limit_outcome(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        reserved_error=ReservedPolicy.CLEAR,
    )
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    worker_started = threading.Event()
    release_worker = threading.Event()

    class LimitTaskRunner:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def supports_stream_callbacks(self) -> bool:
            return False

        def run_with_hooks(
            self,
            work_item: Any,
            **_kwargs: Any,
        ) -> RunnerOutcome:
            del work_item
            worker_started.set()
            release_worker.wait()
            return RunnerOutcome(
                status="limit",
                value=None,
                error="memory limit exceeded",
                stdout=None,
                stderr=None,
                returncode=None,
                duration=0.0,
            )

    monkeypatch.setattr(consumer_module, "TaskRunner", LimitTaskRunner)
    inbox.write(json.dumps({"args": []}))

    try:
        task.process_once()
        assert worker_started.wait(timeout=2.0)
        assert task.taskspec.state.status == "running"

        ctrl_in.write(CONTROL_KILL)
        task.process_once()
        assert task._deferred_active_control_command == CONTROL_KILL
    finally:
        release_worker.set()

    _drive_consumer_until(task, lambda: task.taskspec.state.status == "killed")

    responses = [json.loads(message) for message in drain_queue(ctrl_out)]
    assert any(
        response.get("command") == "KILL" and response["status"] == "ack"
        for response in responses
    )
    assert task.taskspec.state.status == "killed"
    assert task._deferred_active_control_command is None
    assert reserved.has_pending() is False
    task.cleanup()


@pytest.mark.parametrize(
    ("command", "expected_status"),
    ((CONTROL_STOP, "cancelled"), (CONTROL_KILL, "killed")),
)
def test_structured_active_stop_kill_defers_until_finalize(
    broker_env,
    unique_tid: str,
    command: str,
    expected_status: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(unique_tid, sys.executable)
    task = Consumer(db_path, spec)
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    request_id = f"{command.lower()}-request"

    task.taskspec.mark_started(pid=0)
    task.taskspec.mark_running(pid=0)
    task._active_work_in_flight = True
    ctrl_in.write(json.dumps({"command": command.lower(), "request_id": request_id}))

    task._poll_active_control_once()

    assert task._deferred_active_control_command == command
    assert task.taskspec.state.status == "running"
    assert drain_queue(ctrl_out) == []
    assert ctrl_in.read_one() is None

    task._active_work_in_flight = False
    task._finalize_deferred_active_control()

    responses = [json.loads(message) for message in drain_queue(ctrl_out)]
    response = next(item for item in responses if item.get("command") == command)
    assert response["status"] == "ack"
    assert response["request_id"] == request_id
    assert task.taskspec.state.status == expected_status
    assert task._deferred_active_control_command is None
    task.cleanup()


@pytest.mark.parametrize(
    ("command", "expected_status"),
    ((CONTROL_STOP, "cancelled"), (CONTROL_KILL, "killed")),
)
def test_deferred_stop_kill_finalizes_persistent_task_on_ok_outcome(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    expected_status: str,
) -> None:
    """A STOP/KILL deferred during active work must be honored even when the
    work outcome is ``ok`` -- a persistent consumer must not exit leaving
    status ``running`` with no terminal event.

    Spec: [MF-3], [STATE.1], [OBS.1]
    """
    db_path, make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        reserved_stop=ReservedPolicy.CLEAR,
        reserved_error=ReservedPolicy.CLEAR,
        persistent=True,
    )
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    outbox = make_queue(spec.io.outputs["outbox"])
    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)
    worker_started = threading.Event()
    release_worker = threading.Event()

    class OkTaskRunner:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def supports_stream_callbacks(self) -> bool:
            return False

        def run_with_hooks(
            self,
            work_item: Any,
            **_kwargs: Any,
        ) -> RunnerOutcome:
            del work_item
            worker_started.set()
            release_worker.wait()
            return RunnerOutcome(
                status="ok",
                value="persistent-ok-result",
                error=None,
                stdout=None,
                stderr=None,
                returncode=0,
                duration=0.0,
            )

    monkeypatch.setattr(consumer_module, "TaskRunner", OkTaskRunner)
    inbox.write(json.dumps({"args": []}))

    try:
        task.process_once()
        assert worker_started.wait(timeout=2.0)
        assert task.taskspec.state.status == "running"

        ctrl_in.write(command)
        task.process_once()
        assert task._deferred_active_control_command == command
    finally:
        release_worker.set()

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == expected_status,
    )

    assert task.taskspec.state.status == expected_status
    assert task._deferred_active_control_command is None
    assert task.should_stop is True

    terminal_envelope_list = terminal_envelopes(ctrl_out, tid=unique_tid, source="task")
    assert len(terminal_envelope_list) == 1
    assert terminal_envelope_list[0]["status"] == expected_status

    ctrl_out_messages = [json.loads(message) for message in drain_queue(ctrl_out)]
    ack_responses = [
        message for message in ctrl_out_messages if message.get("command") == command
    ]
    assert len(ack_responses) == 1
    assert ack_responses[0]["status"] == "ack"

    expected_event = "control_stop" if command == CONTROL_STOP else "control_kill"
    events = [json.loads(msg) for msg in drain_queue(log_queue)]
    terminal_events = [event for event in events if event["event"] == expected_event]
    assert len(terminal_events) == 1
    assert terminal_events[0]["status"] == expected_status

    result_messages = drain_queue(outbox)
    assert len(result_messages) == 1
    assert result_messages[0] == "persistent-ok-result"

    assert reserved.has_pending() is False
    task.cleanup()


def test_deferred_stop_finalizes_one_shot_task_without_double_terminal_emission(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One-shot tasks already emit a terminal event on the ok path via
    ``_finalize_message`` -> ``mark_completed``. A deferred STOP finalized
    afterward must not double-emit a terminal event or overwrite the
    ``completed`` status.

    Spec: [MF-3], [STATE.1], [OBS.1]
    """
    db_path, make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        reserved_stop=ReservedPolicy.CLEAR,
    )
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    outbox = make_queue(spec.io.outputs["outbox"])
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)
    worker_started = threading.Event()
    release_worker = threading.Event()

    class OkTaskRunner:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def supports_stream_callbacks(self) -> bool:
            return False

        def run_with_hooks(
            self,
            work_item: Any,
            **_kwargs: Any,
        ) -> RunnerOutcome:
            del work_item
            worker_started.set()
            release_worker.wait()
            return RunnerOutcome(
                status="ok",
                value="one-shot-ok-result",
                error=None,
                stdout=None,
                stderr=None,
                returncode=0,
                duration=0.0,
            )

    monkeypatch.setattr(consumer_module, "TaskRunner", OkTaskRunner)
    inbox.write(json.dumps({"args": []}))

    try:
        task.process_once()
        assert worker_started.wait(timeout=2.0)
        assert task.taskspec.state.status == "running"

        ctrl_in.write(CONTROL_STOP)
        task.process_once()
        assert task._deferred_active_control_command == CONTROL_STOP
    finally:
        release_worker.set()

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

    assert task.taskspec.state.status == "completed"
    assert task._deferred_active_control_command is None

    terminal_envelope_list = terminal_envelopes(ctrl_out, tid=unique_tid, source="task")
    assert len(terminal_envelope_list) == 1
    assert terminal_envelope_list[0]["status"] == "completed"

    events = [json.loads(msg) for msg in drain_queue(log_queue)]
    terminal_status_events = [
        event
        for event in events
        if event.get("event") in {"work_completed", "control_stop", "control_kill"}
    ]
    assert len(terminal_status_events) == 1
    assert terminal_status_events[0]["event"] == "work_completed"
    assert terminal_status_events[0]["status"] == "completed"

    result_messages = drain_queue(outbox)
    assert len(result_messages) == 1
    assert result_messages[0] == "one-shot-ok-result"

    task.cleanup()


def test_deferred_stop_on_ok_outcome_does_not_requeue_completed_work(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``reserved_policy_on_stop`` is ``requeue``, a STOP deferred during
    active work that completes ``ok`` must NOT requeue the already-consumed
    reserved row (that would duplicate execution). [QUEUE.6] governs
    unfinished work; the reserved row here was already consumed by
    ``_finalize_message`` on the ok path.
    """
    db_path, make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        reserved_stop=ReservedPolicy.REQUEUE,
        persistent=True,
    )
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    outbox = make_queue(spec.io.outputs["outbox"])
    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    worker_started = threading.Event()
    release_worker = threading.Event()

    class OkTaskRunner:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def supports_stream_callbacks(self) -> bool:
            return False

        def run_with_hooks(
            self,
            work_item: Any,
            **_kwargs: Any,
        ) -> RunnerOutcome:
            del work_item
            worker_started.set()
            release_worker.wait()
            return RunnerOutcome(
                status="ok",
                value="requeue-policy-ok-result",
                error=None,
                stdout=None,
                stderr=None,
                returncode=0,
                duration=0.0,
            )

    monkeypatch.setattr(consumer_module, "TaskRunner", OkTaskRunner)
    inbox.write(json.dumps({"args": []}))

    try:
        task.process_once()
        assert worker_started.wait(timeout=2.0)
        assert task.taskspec.state.status == "running"

        ctrl_in.write(CONTROL_STOP)
        task.process_once()
        assert task._deferred_active_control_command == CONTROL_STOP
    finally:
        release_worker.set()

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "cancelled",
    )

    assert task.taskspec.state.status == "cancelled"
    assert inbox.has_pending() is False
    assert reserved.has_pending() is False

    result_messages = drain_queue(outbox)
    assert len(result_messages) == 1
    assert result_messages[0] == "requeue-policy-ok-result"

    task.cleanup()


def test_runner_error_diagnostics_are_written_to_terminal_task_log(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = Consumer(db_path, spec)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)
    diagnostics = {
        "phase": "execute",
        "runner": "host",
        "target_type": "function",
        "message": "runner boom",
    }

    def fake_run_task_for_reactor(_work_item: object) -> tuple[RunnerOutcome, bool]:
        return (
            RunnerOutcome(
                status="error",
                value=None,
                error="runner boom",
                stdout=None,
                stderr=None,
                returncode=None,
                duration=0.0,
                diagnostics=diagnostics,
            ),
            False,
        )

    monkeypatch.setattr(task, "_run_task_for_reactor", fake_run_task_for_reactor)

    with pytest.raises(RuntimeError, match="runner boom"):
        task.run_work_item({"args": ["direct"]})

    events = [json.loads(msg) for msg in drain_queue(log_queue)]
    failed = next(event for event in events if event["event"] == "work_failed")
    assert failed["runner_diagnostics"] == diagnostics
    task.cleanup()


def test_task_failure_leaves_message_in_reserved(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:fail_payload",
        reserved_error=ReservedPolicy.KEEP,
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "failed",
    )

    outbox = make_queue(spec.io.outputs["outbox"])
    assert outbox.read_one() is None

    reserved_queue = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    peeked = reserved_queue.peek_one(with_timestamps=True)
    assert peeked is not None
    assert peeked[0] is not None
    assert task.taskspec.state.status == "failed"


def test_start_token_cleared_on_failure(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        args=["-c", "import sys; sys.exit(2)"],
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    reserved_name = f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}"
    reserved = make_queue(reserved_name)
    outbox = make_queue(spec.io.outputs["outbox"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])

    inbox.write(json.dumps({}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "failed",
    )

    assert task.taskspec.state.status == "failed"
    assert reserved.peek_one() is None
    assert outbox.peek_one() is None
    terminal_raw = ctrl_out.peek_one()
    assert terminal_raw is not None
    terminal = json.loads(terminal_raw)
    assert terminal["type"] == "terminal"
    assert terminal["source"] == "task"
    assert terminal["tid"] == unique_tid
    assert terminal["status"] == "failed"


def test_task_handles_stop_control_message(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
        reserved_stop=ReservedPolicy.KEEP,
    )
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_in.write(CONTROL_STOP)

    task.process_once()

    assert task.should_stop is True
    assert task.taskspec.state.status == "cancelled"
    assert ctrl_in.read_one() is None


def test_task_run_until_stopped_honors_pending_stop_control(
    broker_env, unique_tid: str
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    outbox = make_queue(spec.io.outputs["outbox"])
    ctrl_in = make_queue(spec.io.control["ctrl_in"])

    inbox.write(json.dumps({"args": ["indef"], "kwargs": {"suffix": "!"}}))
    ctrl_in.write(CONTROL_STOP)

    task.run_until_stopped(poll_interval=0.0, max_iterations=5)

    assert outbox.read_one() is None
    assert task.should_stop is True
    assert task.taskspec.state.status == "cancelled"


def test_task_run_until_stopped_waits_through_activity_seam(
    broker_env,
    unique_tid: str,
    monkeypatch,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = Consumer(db_path, spec)
    wait_calls: list[float | None] = []

    def fake_wait(timeout: float | None) -> None:
        wait_calls.append(timeout)
        task.should_stop = True

    monkeypatch.setattr(task, "wait_for_activity", fake_wait)

    task.run_until_stopped(poll_interval=0.25, max_iterations=5)

    assert wait_calls == [0.25]


def test_task_run_until_stopped_uses_next_wait_timeout(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = Consumer(db_path, spec)
    wait_calls: list[float | None] = []

    def fake_wait(timeout: float | None) -> None:
        wait_calls.append(timeout)
        task.should_stop = True

    monkeypatch.setattr(task, "next_wait_timeout", lambda: 0.75)
    monkeypatch.setattr(task, "wait_for_activity", fake_wait)

    task.run_until_stopped(poll_interval=0.25, max_iterations=5)

    assert wait_calls == [0.75]


def test_task_run_until_stopped_waits_for_zero_next_timeout(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = Consumer(db_path, spec)
    wait_calls: list[float | None] = []

    def fake_wait(timeout: float | None) -> None:
        wait_calls.append(timeout)
        task.should_stop = True

    monkeypatch.setattr(task, "next_wait_timeout", lambda: 0.0)
    monkeypatch.setattr(task, "wait_for_activity", fake_wait)

    task.run_until_stopped(poll_interval=0.0, max_iterations=5)

    assert wait_calls == [0.0]


def _drive_consumer_until(
    task: Consumer,
    predicate: Callable[[], bool],
    *,
    timeout: float = 60.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task.process_once()
        if predicate():
            return
        task.wait_for_activity(timeout=0.02)
    raise AssertionError(
        "Consumer did not reach expected state before timeout "
        f"(status={task.taskspec.state.status!r}, "
        f"should_stop={task.should_stop!r}, "
        f"worker_activity={task._has_worker_activity()!r}, "
        f"worker_snapshot={task._worker_activity_snapshot()!r}, "
        f"worker_stacks={_consumer_worker_stacks(task)!r})"
    )


def _consumer_worker_stacks(task: Consumer) -> list[str]:
    """Return worker lane stacks for async drain diagnostics."""

    frames = sys._current_frames()
    stacks: list[str] = []
    for thread in threading.enumerate():
        if not thread.name.startswith(f"weft-worker-{task.tid_short}-"):
            continue
        frame = frames.get(thread.ident)
        if frame is None:
            continue
        stack = "".join(traceback.format_stack(frame, limit=12))
        stacks.append(f"{thread.name}:\n{stack}")
    return stacks


def test_consumer_reactor_responds_to_ping_while_command_work_is_active(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    command_duration = 2.0
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        args=[
            PROCESS_SCRIPT,
            "--duration",
            str(command_duration),
            "--result",
            "reactor-done",
        ],
    )
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    outbox = make_queue(spec.io.outputs["outbox"])
    inbox.write(json.dumps({"args": []}))

    started_at = time.monotonic()
    task.process_once()
    elapsed = time.monotonic() - started_at

    assert elapsed < command_duration * 0.75
    assert task.taskspec.state.status == "running"
    assert task._has_worker_activity() is True
    assert outbox.read_one() is None

    ctrl_in.write(json.dumps({"command": CONTROL_PING, "request_id": "during"}))
    task.process_once()

    responses = [json.loads(msg) for msg in drain_queue(ctrl_out)]
    pong = next(response for response in responses if response["command"] == "PING")
    assert pong["request_id"] == "during"
    assert pong["message"] == "PONG"
    assert pong["task_status"] == "running"

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
        timeout=5.0,
    )

    assert outbox.read_one() == "reactor-done"


def test_consumer_reactor_stop_cancels_active_command_on_main_thread(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        args=[PROCESS_SCRIPT, "--duration", "1.5", "--result", "should-not-finish"],
        reserved_stop=ReservedPolicy.CLEAR,
    )
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    outbox = make_queue(spec.io.outputs["outbox"])
    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    inbox.write(json.dumps({"args": []}))

    task.process_once()
    assert task.taskspec.state.status == "running"

    ctrl_in.write(CONTROL_STOP)
    task.process_once()
    assert task.should_stop is True

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "cancelled",
        timeout=5.0,
    )

    responses = [json.loads(msg) for msg in drain_queue(ctrl_out)]
    stop_response = next(
        response for response in responses if response.get("command") == "STOP"
    )
    assert stop_response["status"] == "ack"
    assert outbox.read_one() is None
    assert reserved.has_pending() is False


def test_consumer_worker_constructs_runner_without_broker_context(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(unique_tid, sys.executable)
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    outbox = make_queue(spec.io.outputs["outbox"])
    captured_context: list[tuple[Any, Any]] = []

    class FakeTaskRunner:
        def __init__(self, **kwargs: Any) -> None:
            captured_context.append((kwargs.get("db_path"), kwargs.get("config")))

        def supports_stream_callbacks(self) -> bool:
            return False

        def run_with_hooks(
            self,
            work_item: Any,
            **_kwargs: Any,
        ) -> RunnerOutcome:
            del work_item
            return RunnerOutcome(
                status="ok",
                value="broker-free",
                error=None,
                stdout=None,
                stderr=None,
                returncode=0,
                duration=0.0,
            )

    monkeypatch.setattr(consumer_module, "TaskRunner", FakeTaskRunner)
    inbox.write(json.dumps({"args": []}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

    assert captured_context == [(None, None)]
    assert outbox.read_one() == "broker-free"


def test_consumer_active_wait_activity_ignores_reserved_work_queue(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(unique_tid, sys.executable)
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    worker_started = threading.Event()
    release_worker = threading.Event()

    class BlockingTaskRunner:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def supports_stream_callbacks(self) -> bool:
            return False

        def run_with_hooks(
            self,
            work_item: Any,
            **_kwargs: Any,
        ) -> RunnerOutcome:
            del work_item
            worker_started.set()
            release_worker.wait(timeout=2.0)
            return RunnerOutcome(
                status="ok",
                value="done",
                error=None,
                stdout=None,
                stderr=None,
                returncode=0,
                duration=0.0,
            )

    monkeypatch.setattr(consumer_module, "TaskRunner", BlockingTaskRunner)
    inbox.write(json.dumps({"args": []}))

    try:
        task.process_once()
        assert worker_started.wait(timeout=2.0)
        assert reserved.has_pending() is True
        assert task._has_pending_messages() is False

        ctrl_in.write(json.dumps({"command": CONTROL_PING, "request_id": "active"}))
        assert task._has_pending_messages() is True
    finally:
        release_worker.set()

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )


def test_consumer_keeps_one_inflight_item_and_commits_in_source_order(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Port the reference single-inflight ordering guarantee to Weft [MF-2]."""
    db_path, make_queue = broker_env
    spec = make_command_taskspec(unique_tid, sys.executable, persistent=True)
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    outbox = make_queue(spec.io.outputs["outbox"])
    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    first_started = threading.Event()
    release_first = threading.Event()

    class OrderedTaskRunner:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def supports_stream_callbacks(self) -> bool:
            return False

        def run_with_hooks(
            self,
            work_item: Any,
            **_kwargs: Any,
        ) -> RunnerOutcome:
            value = str(work_item["args"][0])
            if value == "first":
                first_started.set()
                assert release_first.wait(timeout=2.0)
            return RunnerOutcome(
                status="ok",
                value=value,
                error=None,
                stdout=None,
                stderr=None,
                returncode=0,
                duration=0.0,
            )

    monkeypatch.setattr(consumer_module, "TaskRunner", OrderedTaskRunner)
    inbox.write(json.dumps({"args": ["first"]}))
    inbox.write(json.dumps({"args": ["second"]}))

    try:
        task.process_once()
        assert first_started.wait(timeout=2.0)
        assert reserved.peek_one() == json.dumps({"args": ["first"]})
        assert inbox.peek_one() == json.dumps({"args": ["second"]})

        task.process_once()
        assert reserved.peek_one() == json.dumps({"args": ["first"]})
        assert inbox.peek_one() == json.dumps({"args": ["second"]})

        release_first.set()
        _drive_consumer_until(task, lambda: outbox.peek_one() is not None)
        assert outbox.read_one() == "first"

        _drive_consumer_until(task, lambda: outbox.peek_one() is not None)
        assert outbox.read_one() == "second"
        assert reserved.peek_one() is None
        assert inbox.peek_one() is None
    finally:
        release_first.set()
        task.cleanup()


def test_consumer_active_control_gets_turn_while_stream_events_remain(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, make_queue = broker_env
    monkeypatch.setattr(base_module, "TASK_WORKER_RESULT_QUEUE_MAXSIZE", 16)
    monkeypatch.setattr(base_module, "TASK_WORKER_RESULT_DRAIN_MAX_PER_TURN", 2)
    spec = make_command_taskspec(unique_tid, sys.executable, stream_output=True)
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    release_worker = threading.Event()

    class StreamingTaskRunner:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def supports_stream_callbacks(self) -> bool:
            return True

        def run_with_hooks(
            self,
            work_item: Any,
            **kwargs: Any,
        ) -> RunnerOutcome:
            del work_item
            on_stdout_chunk = kwargs["on_stdout_chunk"]
            for index in range(8):
                on_stdout_chunk(f"chunk-{index}", False)
            release_worker.wait(timeout=2.0)
            return RunnerOutcome(
                status="ok",
                value="stream-done",
                error=None,
                stdout=None,
                stderr=None,
                returncode=0,
                duration=0.0,
            )

    monkeypatch.setattr(consumer_module, "TaskRunner", StreamingTaskRunner)
    inbox.write(json.dumps({"args": []}))

    try:
        task.process_once()
        deadline = time.monotonic() + 2.0
        while task._worker_result_queue.qsize() < 6 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert task._worker_result_queue.qsize() >= 6

        ctrl_in.write(json.dumps({"command": CONTROL_PING, "request_id": "stream"}))
        task.process_once()

        responses = [json.loads(msg) for msg in drain_queue(ctrl_out)]
        pong = next(response for response in responses if response["command"] == "PING")
        assert pong["request_id"] == "stream"
        assert pong["message"] == "PONG"
        assert task._has_pending_worker_results() is True
    finally:
        release_worker.set()

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )


def test_base_task_applies_worker_result_on_main_thread(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = ReactorTestTask(db_path, spec)
    main_thread_id = threading.get_ident()
    worker_started = threading.Event()

    def worker_body() -> dict[str, int]:
        worker_started.set()
        return {"thread_id": threading.get_ident()}

    task._submit_worker_call("unit", worker_body)
    assert worker_started.wait(timeout=2.0)

    deadline = time.monotonic() + 2.0
    while not task.worker_results and time.monotonic() < deadline:
        task.process_once()
        if not task.worker_results:
            task.wait_for_activity(timeout=0.01)

    assert task.worker_results
    result = task.worker_results[0]
    assert result.lane == "unit"
    assert result.error is None
    assert result.value["thread_id"] != main_thread_id
    assert task.worker_result_thread_ids == [main_thread_id]


def test_base_task_worker_lane_applies_result_on_main_thread(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = ReactorTestTask(db_path, spec)
    main_thread_id = threading.get_ident()
    worker_started = threading.Event()

    def worker_body() -> dict[str, int]:
        worker_started.set()
        return {"thread_id": threading.get_ident()}

    task._submit_worker_lane("unit", worker_body)
    assert worker_started.wait(timeout=2.0)

    deadline = time.monotonic() + 2.0
    while not task.worker_results and time.monotonic() < deadline:
        task.process_once()
        if not task.worker_results:
            task.wait_for_activity(timeout=0.01)

    assert task.worker_results
    result = task.worker_results[0]
    assert result.lane == "unit"
    assert result.error is None
    assert result.value["thread_id"] != main_thread_id
    assert task.worker_result_thread_ids == [main_thread_id]


def test_base_task_worker_lane_delivers_errors_on_main_thread(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = ErrorRecordingReactorTestTask(db_path, spec)
    main_thread_id = threading.get_ident()
    worker_started = threading.Event()

    def worker_body() -> None:
        worker_started.set()
        raise ValueError("lane failed")

    task._submit_worker_lane("unit", worker_body)
    assert worker_started.wait(timeout=2.0)

    deadline = time.monotonic() + 2.0
    while not task.worker_results and time.monotonic() < deadline:
        task.process_once()
        if not task.worker_results:
            task.wait_for_activity(timeout=0.01)

    assert task.worker_results
    result = task.worker_results[0]
    assert result.lane == "unit"
    assert isinstance(result.error, ValueError)
    assert str(result.error) == "lane failed"
    assert task.worker_result_thread_ids == [main_thread_id]


def test_base_task_wait_for_activity_caps_wait_while_worker_is_active(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = ReactorTestTask(db_path, spec)
    worker_started = threading.Event()
    release_worker = threading.Event()
    monkeypatch.setattr(base_module, "TASK_REACTOR_WAKEUP_MAX_SECONDS", 0.02)

    def worker_body() -> str:
        worker_started.set()
        release_worker.wait(timeout=2.0)
        return "done"

    task._submit_worker_call("blocked", worker_body)
    assert worker_started.wait(timeout=2.0)

    started_at = time.monotonic()
    task._wait_for_reactor_activity(timeout=5.0)
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.5
    assert task.worker_results == []

    release_worker.set()
    deadline = time.monotonic() + 2.0
    while not task.worker_results and time.monotonic() < deadline:
        task.process_once()
        if not task.worker_results:
            task.wait_for_activity(timeout=0.01)

    assert task.worker_results[0].value == "done"


def test_base_task_worker_result_drain_is_budgeted(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    monkeypatch.setattr(base_module, "TASK_WORKER_RESULT_QUEUE_MAXSIZE", 10)
    monkeypatch.setattr(base_module, "TASK_WORKER_RESULT_DRAIN_MAX_PER_TURN", 2)
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = ReactorTestTask(db_path, spec)

    for value in range(5):
        assert task._publish_worker_result("unit", value=value) is True

    assert task._drain_worker_results() == 2
    assert [result.value for result in task.worker_results] == [0, 1]
    assert task._has_pending_worker_results() is True

    assert task._drain_worker_results() == 2
    assert [result.value for result in task.worker_results] == [0, 1, 2, 3]
    assert task._has_pending_worker_results() is True

    assert task._drain_worker_results() == 1
    assert [result.value for result in task.worker_results] == [0, 1, 2, 3, 4]
    assert task._has_pending_worker_results() is False


def test_base_task_process_once_spends_one_worker_result_budget_per_turn(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    monkeypatch.setattr(base_module, "TASK_WORKER_RESULT_QUEUE_MAXSIZE", 10)
    monkeypatch.setattr(base_module, "TASK_WORKER_RESULT_DRAIN_MAX_PER_TURN", 2)
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = ReactorTestTask(db_path, spec)

    for value in range(5):
        assert task._publish_worker_result("unit", value=value) is True

    task.process_once()
    assert [result.value for result in task.worker_results] == [0, 1]
    assert task._has_pending_worker_results() is True

    task.process_once()
    assert [result.value for result in task.worker_results] == [0, 1, 2, 3]
    assert task._has_pending_worker_results() is True

    task.process_once()
    assert [result.value for result in task.worker_results] == [0, 1, 2, 3, 4]
    assert task._has_pending_worker_results() is False


def test_base_task_worker_result_queue_backpressures_when_full(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    monkeypatch.setattr(base_module, "TASK_WORKER_RESULT_QUEUE_MAXSIZE", 1)
    monkeypatch.setattr(base_module, "TASK_WORKER_RESULT_DRAIN_MAX_PER_TURN", 1)
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = ReactorTestTask(db_path, spec)
    publisher_finished = threading.Event()

    assert task._publish_worker_result("unit", value="first") is True

    def publish_second() -> None:
        if task._publish_worker_result("unit", value="second"):
            publisher_finished.set()

    publisher = threading.Thread(target=publish_second, daemon=True)
    publisher.start()

    try:
        time.sleep(0.05)
        assert publisher_finished.is_set() is False

        assert task._drain_worker_results() == 1
        assert publisher_finished.wait(timeout=1.0)
        publisher.join(timeout=1.0)

        assert task._drain_worker_results() == 1
        assert [result.value for result in task.worker_results] == [
            "first",
            "second",
        ]
    finally:
        task._worker_stopping.set()


def test_base_task_cleanup_stops_worker_lane(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = ReactorTestTask(db_path, spec)
    worker_started = threading.Event()
    release_worker = threading.Event()

    def worker_body() -> str:
        worker_started.set()
        release_worker.wait(timeout=2.0)
        return "done"

    task._submit_worker_call("blocked", worker_body)
    assert worker_started.wait(timeout=2.0)

    release_worker.set()
    task.cleanup()

    assert not task._has_active_worker_threads()


def test_base_task_worker_error_is_raised_on_main_thread(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, _make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = ReactorTestTask(db_path, spec)
    # Claim drive ownership for this thread before the worker is submitted so
    # the owner-verified wait below is legal even when the result is slow.
    task.process_once()
    worker_started = threading.Event()

    def worker_body() -> str:
        worker_started.set()
        raise ValueError("worker boom")

    task._submit_worker_call("failing", worker_body)
    assert worker_started.wait(timeout=2.0)

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not task._has_pending_worker_results():
        task.wait_for_activity(timeout=0.01)

    with pytest.raises(RuntimeError, match="Task worker lane 'failing' failed") as exc:
        task.process_once()

    assert isinstance(exc.value.__cause__, ValueError)


def test_task_process_entry_waits_through_activity_seam(
    broker_env,
    unique_tid: str,
) -> None:
    global _launcher_process_calls, _launcher_run_calls
    db_path, _make_queue = broker_env
    _launcher_wait_calls.clear()
    _launcher_process_calls = 0
    _launcher_run_calls = 0
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )

    _task_process_entry(
        f"{LauncherWaitTask.__module__}.{LauncherWaitTask.__qualname__}",
        db_path,
        spec.model_dump_json(),
        None,
        0.125,
    )

    assert _launcher_process_calls == 2
    assert _launcher_wait_calls == [0.125]
    assert _launcher_run_calls == 1


def test_task_process_entry_does_not_wait_after_terminal_turn(
    broker_env,
    unique_tid: str,
) -> None:
    global _launcher_process_calls, _launcher_run_calls
    db_path, _make_queue = broker_env
    _launcher_wait_calls.clear()
    _launcher_process_calls = 0
    _launcher_run_calls = 0
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )

    _task_process_entry(
        f"{LauncherTerminalTask.__module__}.{LauncherTerminalTask.__qualname__}",
        db_path,
        spec.model_dump_json(),
        None,
        0.125,
    )

    assert _launcher_process_calls == 1
    assert _launcher_wait_calls == []
    assert _launcher_run_calls == 1


def test_task_process_entry_uses_normal_return_for_windows_hard_exit(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global _launcher_process_calls, _launcher_run_calls
    db_path, _make_queue = broker_env
    _launcher_wait_calls.clear()
    _launcher_process_calls = 0
    _launcher_run_calls = 0
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )

    def _unexpected_exit(_status: int) -> None:
        raise AssertionError("Windows task-process entries must not use os._exit")

    class _WindowsOS:
        """Delegate to the real os module while reporting a Windows os.name.

        Patch the launcher module's ``os`` binding, never the global os
        module: this test drives a real in-process task with live background
        threads, and a global ``os.name = "nt"`` lets pathlib hand out
        WindowsPath objects on POSIX, corrupting any concurrent lazy import.
        """

        def __init__(self) -> None:
            self.name = "nt"
            self._exit = _unexpected_exit

        def __getattr__(self, attr: str) -> Any:
            return getattr(os, attr)

    monkeypatch.setattr(launcher_module, "os", _WindowsOS())

    _task_process_entry(
        f"{LauncherTerminalTask.__module__}.{LauncherTerminalTask.__qualname__}",
        db_path,
        spec.model_dump_json(),
        None,
        0.125,
        True,
    )

    assert _launcher_process_calls == 1
    assert _launcher_run_calls == 1


def test_parent_loss_shutdown_records_without_setting_task_stop_event() -> None:
    class ParentLossTask:
        def __init__(self) -> None:
            self._stop_event = threading.Event()
            self.parent_loss_notes = 0

        def note_parent_loss(self) -> None:
            self.parent_loss_notes += 1

    task = ParentLossTask()

    _request_parent_loss_shutdown(task)

    assert task.parent_loss_notes == 1
    assert task._stop_event.is_set() is False


def test_parent_loss_is_observed_after_bounded_owner_wait(
    broker_env,
    unique_tid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, _make_queue = broker_env
    task = ReactorTestTask(
        db_path,
        make_function_taskspec(
            unique_tid,
            "tests.tasks.sample_targets:echo_payload",
        ),
    )
    task.process_once()
    task.enable_parent_loss_watch()
    entered_wait = threading.Event()
    noted_parent_loss = threading.Event()
    wait_timeouts: list[float | None] = []

    class BlockingWaiter:
        def wait(self, timeout: float | None) -> bool:
            wait_timeouts.append(timeout)
            entered_wait.set()
            threading.Event().wait(timeout=timeout)
            return False

    monkeypatch.setattr(
        task,
        "_ensure_multi_activity_waiter",
        lambda: BlockingWaiter(),
    )

    def note_parent_loss() -> None:
        assert entered_wait.wait(timeout=2.0)
        task.note_parent_loss()
        noted_parent_loss.set()

    notifier = threading.Thread(target=note_parent_loss)
    notifier.start()
    started_at = time.monotonic()
    task.wait_for_activity(timeout=5.0)
    elapsed = time.monotonic() - started_at
    notifier.join(timeout=2.0)

    try:
        assert noted_parent_loss.is_set()
        assert wait_timeouts == [PARENT_LOSS_WAKE_INTERVAL_CEILING]
        assert elapsed < PARENT_LOSS_WAKE_INTERVAL_CEILING + 0.3
        task.process_once()
        assert task.taskspec.state.status == "cancelled"
        assert task._has_pending_termination_request() is False
    finally:
        task.stop()


def test_task_ignores_unknown_control_message(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
    )
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_in.write("UNKNOWN")

    task.process_once()

    assert task.should_stop is False
    assert task.taskspec.state.status == "created"
    assert ctrl_in.read_one() is None


def test_command_target_executes_process(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        args=[PROCESS_SCRIPT],
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    outbox = make_queue(spec.io.outputs["outbox"])
    inbox.write(json.dumps({"args": ["--result", "command-done"]}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

    assert outbox.read_one() == "command-done"


def test_large_output_spills_to_disk(tmp_path, broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    context_root = tmp_path / "project"

    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:large_output",
        cleanup_on_exit=False,
        weft_context=str(context_root),
        output_size_limit_mb=1,
    )

    task = Consumer(db_path, spec)

    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    inbox = make_queue(spec.io.inputs["inbox"])
    output_size = 2 * 1024 * 1024
    inbox.write(json.dumps({"kwargs": {"size": output_size}}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

    outbox = make_queue(spec.io.outputs["outbox"])
    reference = json.loads(outbox.read_one())
    assert reference["type"] == "large_output"
    output_path = Path(reference["path"])
    assert output_path.exists()
    assert output_path.read_bytes() == b"x" * output_size
    assert reference["size"] == output_size

    events = [json.loads(msg) for msg in drain_queue(log_queue)]
    assert any(event["event"] == "output_spilled" for event in events)

    if sys.platform != "win32":
        assert stat.S_IMODE(Path(output_path).stat().st_mode) == 0o600
        assert stat.S_IMODE(Path(output_path).parent.stat().st_mode) == 0o700


def test_large_output_spills_to_custom_weft_directory_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    context_root = tmp_path / "project"
    monkeypatch.setenv("WEFT_DIRECTORY_NAME", ".engram")

    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:large_output",
        cleanup_on_exit=False,
        weft_context=str(context_root),
        output_size_limit_mb=1,
    )

    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    output_size = 2 * 1024 * 1024
    inbox.write(json.dumps({"kwargs": {"size": output_size}}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

    outbox = make_queue(spec.io.outputs["outbox"])
    reference = json.loads(outbox.read_one())
    expected_path = (
        Path(context_root) / ".engram" / "outputs" / unique_tid / "output.dat"
    )

    assert Path(reference["path"]) == expected_path
    assert expected_path.exists()
    assert expected_path.read_bytes() == b"x" * output_size


def test_large_output_cleanup_on_exit(tmp_path, broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    context_root = tmp_path / "project"

    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:large_output",
        cleanup_on_exit=True,
        weft_context=str(context_root),
        output_size_limit_mb=1,
    )

    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"kwargs": {"size": 2 * 1024 * 1024}}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

    expected_path = Path(context_root) / ".weft" / "outputs" / unique_tid / "output.dat"
    assert not expected_path.exists()


def test_stream_output_writes_chunks(tmp_path, broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    context_root = tmp_path / "project"

    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:large_output",
        weft_context=str(context_root),
        output_size_limit_mb=1,
        stream_output=True,
    )

    task = Consumer(db_path, spec)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    inbox = make_queue(spec.io.inputs["inbox"])
    output_size = 2 * 1024 * 1024
    inbox.write(json.dumps({"kwargs": {"size": output_size}}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

    outbox = make_queue(spec.io.outputs["outbox"])
    chunks: list[bytes] = []
    messages: list[dict] = []
    while True:
        message = outbox.read_one()
        if message is None:
            break
        envelope = json.loads(message)
        messages.append(envelope)
        assert envelope["type"] == "stream"
        chunk_bytes = base64.b64decode(envelope["data"])
        chunks.append(chunk_bytes)
        if envelope["final"]:
            break

    assert len(messages) >= 2
    assert messages[-1]["final"] is True
    reconstructed = b"".join(chunks)
    assert reconstructed == b"x" * output_size

    events = [json.loads(msg) for msg in drain_queue(log_queue)]
    completed = next(event for event in events if event["event"] == "work_completed")
    assert completed["result_bytes"] == output_size


def test_stream_output_small_payload_single_chunk(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
        stream_output=True,
    )
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

    outbox = make_queue(spec.io.outputs["outbox"])
    message = outbox.read_one()
    assert message is not None
    envelope = json.loads(message)
    assert envelope["type"] == "stream"
    assert envelope["final"] is True
    decoded = base64.b64decode(envelope["data"]).decode("utf-8")
    assert decoded == "payload"


def test_live_command_streaming_persists_stderr_after_control_cleanup(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        args=[
            "-c",
            "import sys; print('stdout-line'); print('stderr-line', file=sys.stderr)",
        ],
        stream_output=True,
    )
    task = Consumer(db_path, spec)
    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )
    task.cleanup()

    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    outbox = make_queue(spec.io.outputs["outbox"])
    messages = [json.loads(message) for message in outbox.peek_generator()]
    assert any(
        message.get("stream") == "stderr" and message.get("data") == "stderr-line\n"
        for message in messages
    )
    assert ctrl_out.stats().total == 0


def test_streaming_session_records_and_clears(
    monkeypatch, broker_env, unique_tid: str
) -> None:
    writes, deletes = _instrument_streaming_queue(monkeypatch)

    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
        stream_output=True,
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )
    task.cleanup()

    assert writes, "expected streaming session entry"
    session = writes[0]
    assert session["tid"] == unique_tid
    assert session["mode"] == "stream"
    assert session["queue"] == spec.io.outputs["outbox"]
    assert session["session_id"].startswith(
        f"{unique_tid}:{spec.io.outputs['outbox']}:"
    )
    assert deletes, "expected streaming session deletion"


def test_persistent_live_command_streaming_clears_session_before_waiting(
    monkeypatch,
    broker_env,
    unique_tid: str,
) -> None:
    writes, deletes = _instrument_streaming_queue(monkeypatch)

    db_path, _make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        args=[PROCESS_SCRIPT, "--result", "streamed"],
        persistent=True,
        stream_output=True,
    )
    task = Consumer(db_path, spec)

    try:
        result = task.run_work_item({})
        assert result == "streamed"
        assert task.taskspec.state.status == "running"
        assert writes, "expected streaming session entry"
        assert deletes, "expected streaming session deletion before returning"
    finally:
        task.cleanup()


def test_persistent_work_item_success_does_not_emit_terminal_envelope(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        args=[PROCESS_SCRIPT, "--result", "persistent-result"],
        persistent=True,
    )
    task = Consumer(db_path, spec)
    log_queue = make_queue(WEFT_GLOBAL_LOG_QUEUE)
    drain_queue(log_queue)

    try:
        result = task.run_work_item({})

        assert result == "persistent-result"
        assert task.taskspec.state.status == "running"
        events = [json.loads(msg) for msg in drain_queue(log_queue)]
        assert any(event["event"] == "work_item_completed" for event in events)
        ctrl_out = make_queue(spec.io.control["ctrl_out"])
        assert terminal_envelopes(ctrl_out, tid=unique_tid, source="task") == []
    finally:
        task.cleanup()


def test_cleanup_on_exit_removes_output_queue(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
        cleanup_on_exit=True,
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    make_queue(spec.io.outputs["outbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

    assert make_queue(spec.io.outputs["outbox"]).has_pending() is True


def test_cleanup_on_exit_process_target(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        args=[PROCESS_SCRIPT, "--output-size", "1024"],
        cleanup_on_exit=True,
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    make_queue(spec.io.outputs["outbox"])
    inbox.write(json.dumps({"args": []}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )

    assert make_queue(spec.io.outputs["outbox"]).has_pending() is True


def test_task_cleanup_removes_standard_control_queues_after_success(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_command_taskspec(
        unique_tid,
        sys.executable,
        args=[PROCESS_SCRIPT, "--result", "payload"],
        cleanup_on_exit=False,
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    outbox = make_queue(spec.io.outputs["outbox"])
    inbox.write(json.dumps({"args": []}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "completed",
    )
    ctrl_in.write(CONTROL_PING)

    assert ctrl_in.stats().total == 1
    assert ctrl_out.stats().total > 0

    task.cleanup()

    assert ctrl_in.stats().total == 0
    assert ctrl_out.stats().total == 0
    assert outbox.has_pending() is True


def test_task_cleanup_removes_standard_control_queues_after_stop(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
        cleanup_on_exit=False,
    )
    task = Consumer(db_path, spec)

    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_out = make_queue(spec.io.control["ctrl_out"])
    ctrl_in.write(CONTROL_STOP)

    task.process_once()

    assert task.should_stop is True
    assert ctrl_out.stats().total > 0

    task.cleanup()

    assert ctrl_in.stats().total == 0
    assert ctrl_out.stats().total == 0


def test_reserved_policy_keep_on_stop(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
        reserved_stop=ReservedPolicy.KEEP,
    )
    task = Consumer(db_path, spec)

    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    reserved.write("job")
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_in.write(CONTROL_STOP)

    task.process_once()

    assert reserved.has_pending() is True


def test_reserved_policy_clear_on_stop(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
        reserved_stop=ReservedPolicy.CLEAR,
    )
    task = Consumer(db_path, spec)

    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    reserved.write("job")
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_in.write(CONTROL_STOP)

    task.process_once()

    assert reserved.has_pending() is False


def test_reserved_policy_requeue_on_stop(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:echo_payload",
        reserved_stop=ReservedPolicy.REQUEUE,
    )
    task = Consumer(db_path, spec)

    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    reserved.write("job")
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_in.write(CONTROL_STOP)

    task.process_once()

    inbox = make_queue(spec.io.inputs["inbox"])
    assert inbox.read_one() == "job"
    assert reserved.has_pending() is False


def test_stop_with_default_cleanup_preserves_reserved_when_keep(
    broker_env, unique_tid: str
) -> None:
    """cleanup_on_exit=True must not override the KEEP reserved policy on STOP."""
    db_path, make_queue = broker_env
    spec = TaskSpec(
        tid=unique_tid,
        name="task-default-cleanup",
        spec=SpecSection(
            type="function",
            function_target="tests.tasks.sample_targets:echo_payload",
            cleanup_on_exit=True,
            reserved_policy_on_stop=ReservedPolicy.KEEP,
        ),
        io=IOSection(
            inputs={"inbox": f"T{unique_tid}.inbox"},
            outputs={"outbox": f"T{unique_tid}.{QUEUE_OUTBOX_SUFFIX}"},
            control={
                "ctrl_in": f"T{unique_tid}.{QUEUE_CTRL_IN_SUFFIX}",
                "ctrl_out": f"T{unique_tid}.ctrl_out",
            },
        ),
        state=StateSection(),
    )
    task = Consumer(db_path, spec)

    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    reserved.write("job")
    ctrl_in = make_queue(spec.io.control["ctrl_in"])
    ctrl_in.write(CONTROL_STOP)

    task.process_once()

    assert task.should_stop is True
    assert reserved.has_pending() is True


def test_reserved_policy_keep_on_error(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:fail_payload",
        reserved_error=ReservedPolicy.KEEP,
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "failed",
    )

    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    assert reserved.has_pending() is True
    assert task.taskspec.state.status == "failed"


def test_reserved_policy_clear_on_error(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:fail_payload",
        reserved_error=ReservedPolicy.CLEAR,
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "failed",
    )

    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    assert reserved.has_pending() is False
    assert task.taskspec.state.status == "failed"


def test_reserved_policy_requeue_on_error(broker_env, unique_tid: str) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(
        unique_tid,
        "tests.tasks.sample_targets:fail_payload",
        reserved_error=ReservedPolicy.REQUEUE,
    )
    task = Consumer(db_path, spec)

    inbox = make_queue(spec.io.inputs["inbox"])
    inbox.write(json.dumps({"args": ["payload"]}))

    _drive_consumer_until(
        task,
        lambda: task.taskspec.state.status == "failed",
    )

    reserved = make_queue(f"T{unique_tid}.{QUEUE_RESERVED_SUFFIX}")
    assert reserved.has_pending() is False
    assert inbox.read_one() is not None
    assert task.taskspec.state.status == "failed"
