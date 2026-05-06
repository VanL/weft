"""Tests for queue activity wait helpers."""

from __future__ import annotations

import threading

import pytest

from simplebroker import Queue
from weft.core import queue_wait
from weft.core.queue_wait import QueueChangeMonitor

pytestmark = [pytest.mark.shared]


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
    broker_env,
    monkeypatch,
) -> None:
    _db_path, make_queue = broker_env
    queues = [make_queue("monitor.one"), make_queue("monitor.two")]
    fake_waiter = FakeWaiter()
    received: dict[str, object] = {}

    def fake_create(created_queues, *, stop_event):
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
    broker_env,
    monkeypatch,
) -> None:
    _db_path, make_queue = broker_env
    queues = [make_queue("fallback.monitor.one"), make_queue("fallback.monitor.two")]
    created: list[Queue] = []
    stopped = 0

    class FakeQueueWatcher:
        def __init__(
            self,
            queue: Queue,
            _handler,
            *,
            stop_event,
            peek: bool,
            since_timestamp: int,
            config,
        ) -> None:
            del _handler, stop_event, peek, since_timestamp, config
            created.append(queue)

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

    monitor = QueueChangeMonitor(queues)
    monitor.close()
    monitor.close()

    assert created == queues
    assert stopped == 2


def test_queue_change_monitor_close_is_idempotent(
    broker_env,
    monkeypatch,
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
    broker_env,
    monkeypatch,
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
    finally:
        monitor.close()

    assert fake_waiter.close_calls == 1


def test_queue_change_monitor_does_not_consume_messages(
    broker_env,
    monkeypatch,
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
