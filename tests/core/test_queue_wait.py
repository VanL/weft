"""Tests for queue activity wait helpers."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from typing import Any

import pytest

from simplebroker import Config, Queue, QueueWatcher
from tests.helpers.typing import BrokerEnv
from tests.helpers.weft_harness import WeftTestHarness
from weft.core import queue_wait
from weft.core.queue_wait import QueueChangeMonitor

pytestmark = [pytest.mark.shared]


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

        def run_in_thread(self) -> threading.Thread:
            thread = super().run_in_thread()
            threads.append(thread)
            if len(watchers) == 2 and phase == "start":
                raise failure
            return thread

        def stop(self, *, join: bool = True, timeout: float = 2.0) -> None:
            stopped.append(self)
            super().stop(join=join, timeout=timeout)

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
    def wait(self, timeout: float | None) -> bool:
        del timeout
        self.entered.set()
        raise RuntimeError("wait failed")


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
            del _handler, stop_event, peek, after_timestamp
            created.append(queue)
            watcher_configs.append(config)

        def run_in_thread(self) -> None:
            return None

        def stop(self, *, join: bool = True) -> None:
            nonlocal stopped
            del join
            stopped += 1

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


def test_queue_change_monitor_wakes_once_when_waiter_raises(
    broker_env: BrokerEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_path, make_queue = broker_env
    fake_waiter = RaisingWaiter()

    monkeypatch.setattr(
        queue_wait,
        "create_activity_waiter_for_queues",
        lambda queues, *, stop_event: fake_waiter,
    )
    monitor = QueueChangeMonitor([make_queue("monitor.raise")])

    try:
        assert fake_waiter.entered.wait(timeout=1.0)
        assert monitor.wait(timeout=1.0) is True
        # Producer closure makes the absence of a second wake meaningful.
        thread = monitor._monitor_thread
        assert thread is not None
        thread.join(timeout=1.0)
        assert not thread.is_alive()
        assert monitor.wait(timeout=0.0) is False
    finally:
        monitor.close()

    assert fake_waiter.close_calls == 1


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
