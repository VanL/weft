"""Tests for queue activity wait helpers."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from typing import Any

import pytest

from simplebroker import Config, Queue, QueueWatcher
from simplebroker.ext import BrokerError
from tests.helpers.typing import BrokerEnv
from tests.helpers.weft_harness import WeftTestHarness
from weft.core import queue_wait
from weft.core.queue_wait import QueueChangeMonitor

pytestmark = [pytest.mark.shared]


def _record_monitor_thread_starts(
    monkeypatch: pytest.MonkeyPatch,
    threads: list[threading.Thread],
    after_start: Callable[[], None],
) -> None:
    """Inject startup failure after a real monitor thread has acquired custody."""
    original_start = threading.Thread.start

    def start(thread: threading.Thread) -> None:
        target = getattr(thread, "_target", None)
        if isinstance(getattr(target, "__self__", None), QueueChangeMonitor):
            threads.append(thread)
            original_start(thread)
            after_start()
        else:
            original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", start)


def test_monitor_constructor_closes_real_waiter_when_thread_start_fails(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = weft_harness.context
    if context.backend_name != "postgres":
        pytest.skip("Requires a real multi-queue activity waiter")
    original_create = queue_wait.create_activity_waiter_for_queues
    original_start = threading.Thread.start
    waiters: list[Any] = []
    closed_waiters: list[Any] = []
    closed_queues: list[Queue] = []
    failure = RuntimeError("injected monitor thread startup failure")

    def create(queues: Sequence[Queue], *, stop_event: threading.Event) -> Any:
        waiter = original_create(queues, stop_event=stop_event)
        assert waiter is not None
        waiters.append(waiter)
        original_close = waiter.close

        def close() -> None:
            closed_waiters.append(waiter)
            original_close()

        monkeypatch.setattr(waiter, "close", close)
        return waiter

    def start(thread: threading.Thread) -> None:
        target = getattr(thread, "_target", None)
        if isinstance(getattr(target, "__self__", None), QueueChangeMonitor):
            raise failure
        original_start(thread)

    with context.queue("monitor.failed.start", persistent=True) as queue:
        original_close = queue.close

        def close_queue() -> None:
            closed_queues.append(queue)
            original_close()

        monkeypatch.setattr(queue, "close", close_queue)
        monkeypatch.setattr(queue_wait, "create_activity_waiter_for_queues", create)
        monkeypatch.setattr(threading.Thread, "start", start)
        try:
            with pytest.raises(RuntimeError) as exc_info:
                QueueChangeMonitor([queue])
            assert exc_info.value is failure
            assert len(waiters) == 1
            assert closed_waiters == waiters
            assert closed_queues == []
            queue.write("caller still owns queue")
            assert queue.read_one() == "caller still owns queue"
        finally:
            for waiter in waiters:
                waiter.close()


@pytest.mark.parametrize("phase", ["construct", "start"])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_monitor_constructor_stops_real_fallback_watchers_on_failure(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    error_type: type[BaseException],
) -> None:
    _db_path, make_queue = broker_env
    queues = [make_queue("failed.fallback.one"), make_queue("failed.fallback.two")]
    watchers: list[QueueWatcher] = []
    threads: list[threading.Thread] = []
    stopped: list[QueueWatcher] = []
    closed_queues: list[Queue] = []
    failure = error_type("injected later watcher failure")
    original_close = Queue.close

    class FailingWatcher(QueueWatcher):
        def __init__(self, queue: Queue, *args: Any, **kwargs: Any) -> None:
            if queue is queues[1] and phase == "construct":
                raise failure
            super().__init__(queue, *args, **kwargs)
            watchers.append(self)

        def stop(self, *, join: bool = True, timeout: float = 2.0) -> None:
            stopped.append(self)
            super().stop(join=join, timeout=timeout)

    def after_start() -> None:
        if len(watchers) == 2 and phase == "start":
            raise failure

    def close_queue(queue: Queue) -> None:
        if any(queue is caller_queue for caller_queue in queues):
            closed_queues.append(queue)
        original_close(queue)

    monkeypatch.setattr(
        queue_wait,
        "create_activity_waiter_for_queues",
        lambda queues, *, stop_event: None,
    )
    monkeypatch.setattr(queue_wait, "QueueWatcher", FailingWatcher)
    _record_monitor_thread_starts(monkeypatch, threads, after_start)
    monkeypatch.setattr(Queue, "close", close_queue)
    try:
        with pytest.raises(error_type) as exc_info:
            QueueChangeMonitor(queues)
        assert exc_info.value is failure
        assert len(watchers) == (1 if phase == "construct" else 2)
        assert stopped == watchers
        assert all(not thread.is_alive() for thread in threads)
        assert closed_queues == []
        for queue in queues:
            # Watcher stop signals cancellation, but does not take queue ownership.
            queue.set_stop_event(None)
            queue.write("caller still owns queue")
            assert queue.read_one() == "caller still owns queue"
    finally:
        for watcher in watchers:
            watcher.stop()


class FakeWaiter:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.wake = threading.Event()
        self.closed = threading.Event()
        self.close_calls = 0

    def wait(self, timeout: float | None) -> bool:
        self.entered.set()
        self.wake.wait(timeout)
        return self.wake.is_set()

    def close(self) -> None:
        self.close_calls += 1
        self.closed.set()
        self.wake.set()


class RaisingWaiter(FakeWaiter):
    def __init__(self, failure: BaseException) -> None:
        super().__init__()
        self.failure = failure

    def wait(self, timeout: float | None) -> bool:
        del timeout
        self.entered.set()
        raise self.failure


@pytest.mark.parametrize("source", ["native", "fallback"])
@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
def test_observer_fatal_exit_stays_on_thread_and_wakes_waiter(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    error_type: type[BaseException],
) -> None:
    """Fatal thread exit is not transferred to the caller or hidden as idle."""
    _db_path, make_queue = broker_env
    fatal = error_type("observer thread stopped")
    boundary_seen = threading.Event()
    at_boundary: list[BaseException] = []
    waiter = RaisingWaiter(fatal)
    stopped: list[object] = []

    original_hook = threading.excepthook

    def thread_boundary(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is fatal:
            at_boundary.append(fatal)
            boundary_seen.set()
        else:
            original_hook(args)

    class FatalWatcher:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        def run_forever(self) -> None:
            raise fatal

        def stop(self, *, join: bool = True) -> None:
            del join
            stopped.append(self)

    # Observe the real thread boundary explicitly instead of allowing pytest's
    # warning hook to mask whether the monitor swallowed the fatal exception.
    monkeypatch.setattr(threading, "excepthook", thread_boundary)
    monkeypatch.setattr(
        queue_wait,
        "create_activity_waiter_for_queues",
        lambda *args, **kwargs: waiter if source == "native" else None,
    )
    monkeypatch.setattr(queue_wait, "QueueWatcher", FatalWatcher)
    monitor = QueueChangeMonitor([make_queue("monitor.fatal")])
    try:
        assert boundary_seen.wait(1.0)
        assert at_boundary == [fatal]
        for timeout in (1.0, 0.0):
            with pytest.raises(BrokerError) as exc_info:
                monitor.wait(timeout)
            assert isinstance(exc_info.value.__cause__, RuntimeError)
            assert exc_info.value.__cause__ is not fatal
    finally:
        monitor.close()
    if source == "native":
        assert waiter.close_calls == 1
    else:
        assert len(stopped) == 1


def test_queue_change_monitor_uses_multi_queue_waiter(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_path, make_queue = broker_env
    queues = [make_queue("monitor.one"), make_queue("monitor.two")]
    fake_waiter = FakeWaiter()
    received: dict[str, object] = {}

    def fake_create(
        created_queues: Sequence[Queue], *, stop_event: threading.Event
    ) -> FakeWaiter:
        received["queues"] = created_queues
        received["stop_event"] = stop_event
        return fake_waiter

    monkeypatch.setattr(queue_wait, "create_activity_waiter_for_queues", fake_create)
    monitor = QueueChangeMonitor(queues)

    try:
        assert fake_waiter.entered.wait(timeout=1.0)
        fake_waiter.wake.set()
        assert monitor.wait(timeout=1.0) is True
    finally:
        monitor.close()

    assert received["queues"] == queues
    assert isinstance(received["stop_event"], threading.Event)
    assert fake_waiter.close_calls == 1
    assert monitor._monitor_thread is None


def test_queue_change_monitor_falls_back_to_queue_watchers(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_path, make_queue = broker_env
    queues = [make_queue("fallback.monitor.one"), make_queue("fallback.monitor.two")]
    created: list[Queue] = []
    watcher_configs: list[object] = []
    stopped = 0

    class FakeQueueWatcher:
        def __init__(
            self,
            queue: Queue,
            _handler: Callable[[str], None],
            *,
            stop_event: threading.Event,
            peek: bool,
            after_timestamp: int,
            config: Config,
        ) -> None:
            del _handler, peek, after_timestamp
            self.stop_event = stop_event
            created.append(queue)
            watcher_configs.append(config)

        def run_forever(self) -> None:
            self.stop_event.wait()

        def stop(self, *, join: bool = True) -> None:
            nonlocal stopped
            del join
            stopped += 1
            self.stop_event.set()

    monkeypatch.setattr(
        queue_wait,
        "create_activity_waiter_for_queues",
        lambda created_queues, *, stop_event: None,
    )
    monkeypatch.setattr(queue_wait, "QueueWatcher", FakeQueueWatcher)
    monkeypatch.setenv("BROKER_CACHE_MB", "not-an-integer")

    monitor = QueueChangeMonitor(queues)
    monitor.close()
    monitor.close()

    assert created == queues
    assert all(isinstance(config, Config) for config in watcher_configs)
    assert stopped == 2


def test_queue_change_monitor_close_is_idempotent(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_path, make_queue = broker_env
    fake_waiter = FakeWaiter()

    monkeypatch.setattr(
        queue_wait,
        "create_activity_waiter_for_queues",
        lambda queues, *, stop_event: fake_waiter,
    )
    monitor = QueueChangeMonitor([make_queue("monitor.close")])

    monitor.close()
    monitor.close()

    assert fake_waiter.close_calls == 1


@pytest.mark.parametrize("error_type", [RuntimeError, TypeError, ValueError])
def test_queue_change_monitor_reports_native_failure_on_every_wait(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    _db_path, make_queue = broker_env
    failure = error_type("wait failed")
    fake_waiter = RaisingWaiter(failure)

    monkeypatch.setattr(
        queue_wait,
        "create_activity_waiter_for_queues",
        lambda queues, *, stop_event: fake_waiter,
    )
    monitor = QueueChangeMonitor([make_queue("monitor.raise")])

    try:
        assert fake_waiter.entered.wait(timeout=1.0)
        # A lost observer is an operational failure, including causes that the
        # command boundary would otherwise misclassify as invalid input.
        for timeout in (1.0, 0.0):
            with pytest.raises(BrokerError) as exc_info:
                monitor.wait(timeout=timeout)
            assert exc_info.value.__cause__ is failure
    finally:
        monitor.close()

    assert fake_waiter.close_calls == 1


@pytest.mark.parametrize("failure_mode", ["raises", "returns"])
def test_queue_change_monitor_reports_fallback_observer_exit(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    _db_path, make_queue = broker_env
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    stopped: list[object] = []
    threads: list[threading.Thread] = []
    failure = RuntimeError("fallback observation failed")

    class FailingWatcher:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        def run_forever(self) -> None:
            entered.set()
            try:
                release.wait()
                if failure_mode == "raises":
                    raise failure
            finally:
                finished.set()

        def run_in_thread(self) -> threading.Thread:
            # The old monitor delegates thread ownership to the dependency.
            # Catch here only to keep its unobserved RED failure out of pytest's
            # unhandled-thread warning path; the new target calls run_forever.
            def run() -> None:
                try:
                    self.run_forever()
                except RuntimeError:
                    pass

            thread = threading.Thread(target=run, daemon=True)
            threads.append(thread)
            thread.start()
            return thread

        def stop(self, *, join: bool = True) -> None:
            del join
            stopped.append(self)
            release.set()

    monkeypatch.setattr(
        queue_wait, "create_activity_waiter_for_queues", lambda *a, **k: None
    )
    monkeypatch.setattr(queue_wait, "QueueWatcher", FailingWatcher)
    monitor = QueueChangeMonitor([make_queue("monitor.fallback.failure")])
    try:
        assert entered.wait(1.0)
        release.set()
        assert finished.wait(1.0)
        with pytest.raises(BrokerError) as exc_info:
            monitor.wait(timeout=1.0)
        if failure_mode == "raises":
            assert exc_info.value.__cause__ is failure
        monitor.close()
        monitor.close()
        assert len(stopped) == 1
    finally:
        release.set()
        monitor.close()
        for thread in threads:
            thread.join(1.0)


def test_queue_change_monitor_close_wakes_idle_wait_without_deadline(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_path, make_queue = broker_env

    class QuietWaiter(FakeWaiter):
        def wait(self, timeout: float | None) -> bool:
            self.entered.set()
            self.wake.wait(timeout)
            # Native shutdown need not report broker activity. The monitor,
            # rather than an accidental dependency wake verdict, owns close wake.
            return False

    waiter = QuietWaiter()
    monkeypatch.setattr(
        queue_wait, "create_activity_waiter_for_queues", lambda *a, **k: waiter
    )
    monitor = QueueChangeMonitor([make_queue("monitor.close.idle")])
    entered = threading.Event()
    returned = threading.Event()
    event_wait = monitor._activity_event.wait

    def observe_wait(timeout: float | None = None) -> bool:
        entered.set()
        return event_wait(timeout)

    monkeypatch.setattr(monitor._activity_event, "wait", observe_wait)

    def wait() -> None:
        try:
            monitor.wait(timeout=None)
        finally:
            returned.set()

    thread = threading.Thread(target=wait, daemon=True)
    thread.start()
    try:
        assert entered.wait(1.0)
        monitor.close()
        monitor.close()
        assert returned.wait(1.0)
        assert waiter.close_calls == 1
    finally:
        # Ensure even the pre-fix indefinitely blocked caller is cleaned up.
        monitor._activity_event.set()
        thread.join(1.0)
        monitor.close()


def test_queue_change_monitor_explicit_wake_is_harmless_after_close(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_path, make_queue = broker_env
    waiter = FakeWaiter()
    monkeypatch.setattr(
        queue_wait, "create_activity_waiter_for_queues", lambda *a, **k: waiter
    )
    queue = make_queue("monitor.direct.wake")
    monitor = QueueChangeMonitor([queue])
    try:
        monitor.wake()
        assert monitor.wait(timeout=0.0)
        queue.write("caller-owned result")
        monitor.close()
        monitor.wake()
        assert queue.read_one() == "caller-owned result"
        assert waiter.close_calls == 1
    finally:
        monitor.close()


def test_queue_change_monitor_close_before_fallback_run_does_not_restart_observer(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_path, make_queue = broker_env
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class DeferredWatcher(QueueWatcher):
        def run_forever(self) -> None:
            entered.set()
            release.wait()
            try:
                # Close has already called public stop while this watcher was
                # idle. A delayed real run must honor that lifecycle ownership.
                super().run_forever()
            finally:
                finished.set()

        def stop(self, *, join: bool = True, timeout: float = 2.0) -> None:
            try:
                super().stop(join=join, timeout=timeout)
            finally:
                release.set()

    monkeypatch.setattr(
        queue_wait, "create_activity_waiter_for_queues", lambda *a, **k: None
    )
    monkeypatch.setattr(queue_wait, "QueueWatcher", DeferredWatcher)
    queue = make_queue("monitor.close.before.run")
    monitor = QueueChangeMonitor([queue])
    try:
        assert entered.wait(1.0)
        monitor.close()
        assert finished.is_set()
        assert monitor.wait(timeout=0.0)
        queue.set_stop_event(None)
        queue.write("caller retained its lease")
        assert queue.read_one() == "caller retained its lease"
    finally:
        release.set()
        monitor.close()


def test_queue_change_monitor_does_not_consume_messages(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_path, make_queue = broker_env
    queue = make_queue("monitor.consume")
    fake_waiter = FakeWaiter()

    monkeypatch.setattr(
        queue_wait,
        "create_activity_waiter_for_queues",
        lambda queues, *, stop_event: fake_waiter,
    )
    monitor = QueueChangeMonitor([queue])

    try:
        queue.write("payload")
        assert fake_waiter.entered.wait(timeout=1.0)
        fake_waiter.wake.set()
        assert monitor.wait(timeout=1.0) is True
        assert queue.read_one() == "payload"
    finally:
        monitor.close()
