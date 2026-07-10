"""Tests for the enhanced MultiQueueWatcher with queue modes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from simplebroker import (
    create_activity_waiter_for_queues as real_create_activity_waiter,
)
from simplebroker.ext import PollingStrategy
from tests.helpers.test_backend import POSTGRES_TEST_BACKEND, active_test_backend
from weft._constants import QUEUE_PRIORITY_INTERNAL, QUEUE_PRIORITY_NORMAL
from weft.core.tasks.multiqueue_watcher import (
    MultiQueueWatcher,
    QueueMessageContext,
    QueueMode,
)


def run_single_drain(watcher: MultiQueueWatcher) -> None:
    """Helper to run a single drain cycle for deterministic tests."""
    watcher._drain_queue()


class FakeWaiter:
    def __init__(self, *, raises: bool = False, result: bool = False) -> None:
        self.wait_calls: list[float | None] = []
        self.wait_entered = threading.Event()
        self.close_calls = 0
        self.raises = raises
        self.result = result

    def wait(self, timeout: float | None) -> bool:
        self.wait_calls.append(timeout)
        self.wait_entered.set()
        if self.raises:
            raise RuntimeError("waiter failed")
        return self.result

    def close(self) -> None:
        self.close_calls += 1


class BlockingWaiter(FakeWaiter):
    """Activity waiter that exposes wait/close thread ownership."""

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()
        self.close_threads: list[int] = []
        self.wait_thread: int | None = None
        self.close_overlapped_wait = False

    def wait(self, timeout: float | None) -> bool:
        self.wait_calls.append(timeout)
        self.wait_thread = threading.get_ident()
        self.wait_entered.set()
        self.release.wait(timeout=2.0)
        return True

    def close(self) -> None:
        if self.wait_entered.is_set() and not self.release.is_set():
            self.close_overlapped_wait = True
        self.close_threads.append(threading.get_ident())
        super().close()


class RaisingCloseWaiter(BlockingWaiter):
    """Waiter whose one-way close reports an error after taking effect."""

    def __init__(self, error_type: type[Exception]) -> None:
        super().__init__()
        self.error_type = error_type

    def close(self) -> None:
        super().close()
        raise self.error_type("injected close failure")


def test_multi_queue_watcher_uses_base_retry_loop() -> None:
    assert "_run_with_retries" not in MultiQueueWatcher.__dict__


def test_background_add_queue_rebinds_exact_set_on_drive_owner(
    broker_env,
    monkeypatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """A foreign add waits for the drive owner to replace the native waiter."""
    del thread_exception_guard
    db_path, _make_queue = broker_env
    created: list[tuple[tuple[str, ...], BlockingWaiter, int]] = []

    def create_waiter(queues, *, stop_event):
        del stop_event
        waiter = BlockingWaiter()
        created.append(
            (tuple(queue.name for queue in queues), waiter, threading.get_ident())
        )
        return waiter

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        create_waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={
            "dynamic.a": {"handler": lambda *_args: None},
            "dynamic.b": {"handler": lambda *_args: None},
        },
        db=db_path,
    )
    drive = watcher.run_in_thread()
    old_waiter = created[0][1]
    assert old_waiter.wait_entered.wait(timeout=2.0)

    mutation_errors: list[BaseException] = []

    def add_queue() -> None:
        try:
            watcher.add_queue("dynamic.c", lambda *_args: None)
        except BaseException as exc:  # pragma: no cover - asserted below
            mutation_errors.append(exc)

    mutator = threading.Thread(target=add_queue)
    mutator.start()
    mutator.join(timeout=0.05)
    assert mutator.is_alive()
    assert old_waiter.close_calls == 0
    assert old_waiter.close_overlapped_wait is False

    old_waiter.release.set()
    mutator.join(timeout=2.0)
    try:
        assert not mutator.is_alive()
        assert mutation_errors == []
        assert created[-1][0] == ("dynamic.a", "dynamic.b", "dynamic.c")
        assert created[-1][2] == drive.ident
        assert old_waiter.close_calls == 1
        assert old_waiter.close_threads == [drive.ident]
        assert watcher.list_queues() == ["dynamic.a", "dynamic.b", "dynamic.c"]
    finally:
        created[-1][1].release.set()
        watcher.stop()
        drive.join(timeout=2.0)


def test_background_remove_queue_rebinds_exact_remaining_set(
    broker_env,
    monkeypatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """A foreign remove publishes only after the exact waiter is installed."""
    del thread_exception_guard
    db_path, _make_queue = broker_env
    created: list[tuple[tuple[str, ...], BlockingWaiter, int]] = []

    def create_waiter(queues, *, stop_event):
        del stop_event
        waiter = BlockingWaiter()
        created.append(
            (tuple(queue.name for queue in queues), waiter, threading.get_ident())
        )
        return waiter

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        create_waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={
            name: {"handler": lambda *_args: None}
            for name in ("remove.a", "remove.b", "remove.c")
        },
        db=db_path,
    )
    drive = watcher.run_in_thread()
    old_waiter = created[0][1]
    assert old_waiter.wait_entered.wait(timeout=2.0)

    errors: list[BaseException] = []

    def remove_queue() -> None:
        try:
            watcher.remove_queue("remove.b")
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    mutator = threading.Thread(target=remove_queue)
    mutator.start()
    assert mutator.is_alive()
    old_waiter.release.set()
    mutator.join(timeout=2.0)
    try:
        assert not mutator.is_alive()
        assert errors == []
        assert created[-1][0] == ("remove.a", "remove.c")
        assert created[-1][2] == drive.ident
        assert old_waiter.close_calls == 1
        assert old_waiter.close_threads == [drive.ident]
        assert watcher.list_queues() == ["remove.a", "remove.c"]
    finally:
        created[-1][1].release.set()
        watcher.stop()
        drive.join(timeout=2.0)


def test_background_topology_mutations_linearize_in_request_order(
    broker_env,
    monkeypatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """Explicit enqueue order is the owner publication order."""
    del thread_exception_guard
    db_path, _make_queue = broker_env
    created: list[tuple[tuple[str, ...], BlockingWaiter]] = []

    def create_waiter(queues, *, stop_event):
        del stop_event
        waiter = BlockingWaiter()
        created.append((tuple(queue.name for queue in queues), waiter))
        return waiter

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        create_waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"ordered.a": {"handler": lambda *_args: None}},
        db=db_path,
    )
    drive = watcher.run_in_thread()
    assert created[0][1].wait_entered.wait(timeout=2.0)
    completions: list[str] = []

    def add(name: str) -> None:
        watcher.add_queue(name, lambda *_args: None)
        completions.append(name)

    first = threading.Thread(target=add, args=("ordered.b",))
    second = threading.Thread(target=add, args=("ordered.c",))
    first.start()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        with watcher._topology_lock:
            if len(watcher._topology_mutations) == 1:
                break
        time.sleep(0.001)
    else:
        raise AssertionError("first mutation was not enqueued")
    second.start()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        with watcher._topology_lock:
            if len(watcher._topology_mutations) == 2:
                break
        time.sleep(0.001)
    else:
        raise AssertionError("second mutation was not enqueued")

    created[0][1].release.set()
    first.join(timeout=2.0)
    second.join(timeout=2.0)
    try:
        assert not first.is_alive() and not second.is_alive()
        assert set(completions) == {"ordered.b", "ordered.c"}
        assert [signature for signature, _waiter in created] == [
            ("ordered.a",),
            ("ordered.a", "ordered.b"),
            ("ordered.a", "ordered.b", "ordered.c"),
        ]
        assert watcher.list_queues() == ["ordered.a", "ordered.b", "ordered.c"]
    finally:
        created[-1][1].release.set()
        watcher.stop()
        drive.join(timeout=2.0)


def test_background_mutation_after_drive_reservation_waits_for_owner_claim(
    broker_env,
    monkeypatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """A reserved drive prevents caller-thread topology effects before claim."""
    del thread_exception_guard
    db_path, _make_queue = broker_env
    entered = threading.Event()
    release = threading.Event()
    waiter_creations: list[tuple[tuple[str, ...], int]] = []

    class ClaimGatedWatcher(MultiQueueWatcher):
        def run_forever(self) -> None:
            entered.set()
            assert release.wait(timeout=2.0)
            super().run_forever()

    def create_waiter(queues, *, stop_event):
        del stop_event
        waiter_creations.append(
            (tuple(queue.name for queue in queues), threading.get_ident())
        )
        return FakeWaiter()

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        create_waiter,
    )
    watcher = ClaimGatedWatcher(
        queue_configs={"claim.a": {"handler": lambda *_args: None}},
        db=db_path,
    )
    constructor_creation_count = len(waiter_creations)
    drive = watcher.run_in_thread()
    assert entered.wait(timeout=2.0)
    errors: list[BaseException] = []

    def mutate() -> None:
        try:
            watcher.add_queue("claim.c", lambda *_args: None)
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    mutator = threading.Thread(target=mutate)
    mutator.start()
    mutator.join(timeout=0.05)
    assert mutator.is_alive()
    assert len(waiter_creations) == constructor_creation_count
    assert watcher.get_queue("claim.c") is None

    release.set()
    mutator.join(timeout=2.0)
    try:
        assert not mutator.is_alive()
        assert errors == []
        assert waiter_creations[-1] == (("claim.a", "claim.c"), drive.ident)
        assert watcher.list_queues() == ["claim.a", "claim.c"]
    finally:
        watcher.stop()
        drive.join(timeout=2.0)


def test_background_rebind_before_first_strategy_start_closes_unbound_cached_waiter(
    broker_env,
    monkeypatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """First-start rebind closes the constructor cache that strategy never owned."""
    del thread_exception_guard
    db_path, _make_queue = broker_env
    gate_entered = threading.Event()
    gate_release = threading.Event()
    created: list[tuple[tuple[str, ...], BlockingWaiter, int]] = []

    class StartGatedWatcher(MultiQueueWatcher):
        def _create_activity_waiter(self, queue):
            gate_entered.set()
            assert gate_release.wait(timeout=2.0)
            return super()._create_activity_waiter(queue)

    def create_waiter(queues, *, stop_event):
        del stop_event
        waiter = BlockingWaiter()
        created.append(
            (tuple(queue.name for queue in queues), waiter, threading.get_ident())
        )
        return waiter

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        create_waiter,
    )
    watcher = StartGatedWatcher(
        queue_configs={"first.a": {"handler": lambda *_args: None}},
        db=db_path,
    )
    old_cached = created[0][1]
    drive = watcher.run_in_thread()
    assert gate_entered.wait(timeout=2.0)
    errors: list[BaseException] = []

    def mutate() -> None:
        try:
            watcher.add_queue("first.c", lambda *_args: None)
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    mutator = threading.Thread(target=mutate)
    mutator.start()
    mutator.join(timeout=0.05)
    assert mutator.is_alive()
    gate_release.set()
    mutator.join(timeout=2.0)
    try:
        assert not mutator.is_alive()
        assert errors == []
        assert created[-1][0] == ("first.a", "first.c")
        assert created[-1][2] == drive.ident
        assert old_cached.close_calls == 1
        assert old_cached.close_threads == [drive.ident]
        assert watcher._strategy.uses_native_activity() is True
    finally:
        created[-1][1].release.set()
        watcher.stop()
        drive.join(timeout=2.0)


def test_background_startup_mutation_applies_before_initial_drain(
    broker_env,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """The drain-entry safe point publishes queued startup mutations first."""
    del thread_exception_guard
    db_path, make_queue = broker_env
    waiter_created = threading.Event()
    release_startup = threading.Event()
    handled = threading.Event()
    observed_membership: list[list[str]] = []

    class StartupGatedWatcher(MultiQueueWatcher):
        def _create_activity_waiter(self, queue):
            waiter = super()._create_activity_waiter(queue)
            waiter_created.set()
            assert release_startup.wait(timeout=2.0)
            return waiter

    watcher: StartupGatedWatcher

    def handle_a(*_args) -> None:
        observed_membership.append(watcher.list_queues())
        handled.set()

    watcher = StartupGatedWatcher(
        queue_configs={"startup.a": {"handler": handle_a}},
        db=db_path,
        inactive_probe_interval=0.01,
    )
    make_queue("startup.a").write("preexisting")
    drive = watcher.run_in_thread()
    assert waiter_created.wait(timeout=2.0)
    mutator = threading.Thread(
        target=lambda: watcher.add_queue("startup.c", lambda *_args: None)
    )
    mutator.start()
    mutator.join(timeout=0.05)
    assert mutator.is_alive()
    release_startup.set()
    mutator.join(timeout=2.0)
    try:
        assert not mutator.is_alive()
        assert handled.wait(timeout=2.0)
        assert observed_membership == [["startup.a", "startup.c"]]
    finally:
        watcher.stop()
        drive.join(timeout=2.0)


@pytest.mark.parametrize("second_entry", ["run", "run_in_thread"])
def test_background_topology_second_drive_entry_is_rejected_without_replacing_owner(
    broker_env,
    second_entry: str,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """A second drive entry cannot replace the live owner or thread reference."""
    del thread_exception_guard
    db_path, make_queue = broker_env
    handled = threading.Event()
    watcher = MultiQueueWatcher(
        queue_configs={"second.a": {"handler": lambda *_args: handled.set()}},
        db=db_path,
        inactive_probe_interval=0.01,
    )
    drive = watcher.run_in_thread()
    deadline = time.monotonic() + 2.0
    while watcher._topology_owner_thread is None and time.monotonic() < deadline:
        time.sleep(0.001)
    original_ref = watcher._thread

    try:
        with pytest.raises(RuntimeError, match="drive owner"):
            if second_entry == "run":
                watcher.run()
            else:
                watcher.run_in_thread()
        assert watcher._thread is original_ref
        assert watcher._topology_owner_thread is drive
        make_queue("second.a").write("work")
        assert handled.wait(timeout=2.0)
        assert drive.is_alive()
    finally:
        watcher.stop()
        drive.join(timeout=2.0)


def test_background_topology_thread_start_failure_rolls_back_drive_reservation(
    broker_env,
    monkeypatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """A failed Thread.start clears only its own reservation and weak reference."""
    del thread_exception_guard
    db_path, make_queue = broker_env
    handled = threading.Event()
    watcher = MultiQueueWatcher(
        queue_configs={"start.a": {"handler": lambda *_args: handled.set()}},
        db=db_path,
        inactive_probe_interval=0.01,
    )
    original_start = threading.Thread.start

    def fail_start(thread: threading.Thread) -> None:
        assert watcher._topology_reserved_thread is thread
        assert watcher._thread is not None and watcher._thread() is thread
        raise RuntimeError("injected start failure")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    with pytest.raises(RuntimeError, match="injected start failure"):
        watcher.run_in_thread()
    assert watcher._topology_reserved_thread is None
    assert watcher._thread is None

    monkeypatch.setattr(threading.Thread, "start", original_start)
    drive = watcher.run_in_thread()
    make_queue("start.a").write("work")
    try:
        assert handled.wait(timeout=2.0)
    finally:
        watcher.stop()
        drive.join(timeout=2.0)


def test_background_add_with_preexisting_message_forces_immediate_discovery(
    broker_env,
    monkeypatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """A newly added queue's existing backlog is drained without a second wake."""
    del thread_exception_guard
    db_path, make_queue = broker_env
    handled = threading.Event()
    created: list[BlockingWaiter] = []

    def create_waiter(queues, *, stop_event):
        del queues, stop_event
        waiter = BlockingWaiter()
        created.append(waiter)
        return waiter

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        create_waiter,
    )
    make_queue("backlog.c").write("already-there")
    watcher = MultiQueueWatcher(
        queue_configs={"backlog.a": {"handler": lambda *_args: None}},
        db=db_path,
        inactive_probe_interval=60.0,
    )
    watcher._next_inactive_probe_at = time.monotonic() + 60.0
    drive = watcher.run_in_thread()
    assert created[0].wait_entered.wait(timeout=2.0)
    mutation_errors: list[BaseException] = []

    def mutate() -> None:
        try:
            watcher.add_queue(
                "backlog.c",
                lambda message, *_args: (
                    handled.set() if message == "already-there" else None
                ),
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            mutation_errors.append(exc)

    mutator = threading.Thread(target=mutate)
    mutator.start()
    created[0].release.set()
    mutator.join(timeout=2.0)
    try:
        assert not mutator.is_alive()
        assert mutation_errors == []
        assert handled.wait(timeout=2.0)
    finally:
        created[-1].release.set()
        watcher.stop()
        drive.join(timeout=2.0)


@pytest.mark.parametrize("fallback", ["none", "raise"])
def test_background_rebind_unavailable_falls_back_then_later_generation_restores_native_wait(
    broker_env,
    monkeypatch,
    fallback: str,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """Native waiter unavailability preserves topology and later recovery."""
    del thread_exception_guard
    db_path, make_queue = broker_env
    c_handled = threading.Event()
    calls: list[tuple[str, ...]] = []
    native_waiters: list[BlockingWaiter] = []

    def create_waiter(queues, *, stop_event):
        del stop_event
        signature = tuple(queue.name for queue in queues)
        calls.append(signature)
        if signature == ("fallback.a", "fallback.b", "fallback.c"):
            if fallback == "raise":
                raise OSError("native waiter unavailable")
            return None
        waiter = BlockingWaiter()
        native_waiters.append(waiter)
        return waiter

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        create_waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={
            "fallback.a": {"handler": lambda *_args: None},
            "fallback.b": {"handler": lambda *_args: None},
        },
        db=db_path,
        inactive_probe_interval=0.01,
    )
    old_waiter = native_waiters[0]
    drive = watcher.run_in_thread()
    assert old_waiter.wait_entered.wait(timeout=2.0)

    add_c = threading.Thread(
        target=lambda: watcher.add_queue("fallback.c", lambda *_args: c_handled.set())
    )
    add_c.start()
    old_waiter.release.set()
    add_c.join(timeout=2.0)
    assert not add_c.is_alive()
    assert watcher._strategy.uses_native_activity() is False
    assert watcher.list_queues() == ["fallback.a", "fallback.b", "fallback.c"]
    assert old_waiter.close_threads == [drive.ident]

    make_queue("fallback.c").write("poll-me")
    assert c_handled.wait(timeout=2.0)
    watcher.add_queue("fallback.d", lambda *_args: None)
    try:
        assert calls[-1] == (
            "fallback.a",
            "fallback.b",
            "fallback.c",
            "fallback.d",
        )
        assert watcher._strategy.uses_native_activity() is True
    finally:
        native_waiters[-1].release.set()
        watcher.stop()
        drive.join(timeout=2.0)


def test_background_queue_open_failure_preserves_old_generation(
    broker_env,
    monkeypatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """A real Queue construction failure leaves the live generation unchanged."""
    del thread_exception_guard
    db_path, make_queue = broker_env
    handled = threading.Event()
    old_waiter = BlockingWaiter()

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        lambda _queues, *, stop_event: old_waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"open.a": {"handler": lambda *_args: handled.set()}},
        db=db_path,
        inactive_probe_interval=0.01,
    )
    generation = watcher._queue_generation
    signature = watcher._multi_activity_waiter_signature
    original_queue = __import__(
        "weft.core.tasks.multiqueue_watcher", fromlist=["Queue"]
    ).Queue

    def fail_c(name, *args, **kwargs):
        if name == "open.c":
            raise OSError("injected queue open failure")
        return original_queue(name, *args, **kwargs)

    monkeypatch.setattr("weft.core.tasks.multiqueue_watcher.Queue", fail_c)
    drive = watcher.run_in_thread()
    assert old_waiter.wait_entered.wait(timeout=2.0)
    caught: list[BaseException] = []

    def mutate() -> None:
        try:
            watcher.add_queue("open.c", lambda *_args: None)
        except BaseException as exc:  # pragma: no cover - asserted below
            caught.append(exc)

    mutator = threading.Thread(target=mutate)
    mutator.start()
    old_waiter.release.set()
    mutator.join(timeout=2.0)
    try:
        assert len(caught) == 1 and isinstance(caught[0], OSError)
        assert watcher.list_queues() == ["open.a"]
        assert watcher._queue_generation == generation
        assert watcher._multi_activity_waiter_signature == signature
        assert watcher._multi_activity_waiter is old_waiter
        assert old_waiter.close_calls == 0
        make_queue("open.a").write("still-live")
        assert handled.wait(timeout=2.0)
    finally:
        watcher.stop()
        drive.join(timeout=2.0)


def test_background_strategy_replacement_failure_fails_request_and_retries_drive(
    broker_env,
    monkeypatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """An exception-atomic replacement failure rolls back candidate resources."""
    del thread_exception_guard
    db_path, make_queue = broker_env
    stop_event = threading.Event()
    handled = threading.Event()
    waiters: list[FakeWaiter] = []

    class FailOnceStrategy(PollingStrategy):
        def __init__(self) -> None:
            super().__init__(stop_event)
            self.fail_next_replace = True
            self.start_calls = 0

        def start(self, *args, **kwargs) -> None:
            self.start_calls += 1
            super().start(*args, **kwargs)

        def replace_activity_waiter(self, activity_waiter):
            if self.fail_next_replace:
                self.fail_next_replace = False
                raise RuntimeError("injected replacement failure")
            return super().replace_activity_waiter(activity_waiter)

    def create_waiter(_queues, *, stop_event):
        del stop_event
        waiter: FakeWaiter
        if not waiters:
            waiter = BlockingWaiter()
        else:
            waiter = FakeWaiter()
        waiters.append(waiter)
        return waiter

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        create_waiter,
    )
    strategy = FailOnceStrategy()
    watcher = MultiQueueWatcher(
        queue_configs={"replace.a": {"handler": lambda *_args: handled.set()}},
        db=db_path,
        stop_event=stop_event,
        polling_strategy=strategy,
        inactive_probe_interval=0.01,
    )
    generation = watcher._queue_generation
    old_waiter = waiters[0]
    assert isinstance(old_waiter, BlockingWaiter)
    drive = watcher.run_in_thread()
    assert old_waiter.wait_entered.wait(timeout=2.0)
    caught: list[BaseException] = []

    def mutate() -> None:
        try:
            watcher.add_queue("replace.c", lambda *_args: None)
        except BaseException as exc:  # pragma: no cover - asserted below
            caught.append(exc)

    mutator = threading.Thread(target=mutate)
    mutator.start()
    old_waiter.release.set()
    mutator.join(timeout=2.0)
    try:
        assert len(caught) == 1
        assert isinstance(caught[0], RuntimeError)
        assert str(caught[0]) == "injected replacement failure"
        assert watcher.list_queues() == ["replace.a"]
        assert watcher._queue_generation == generation
        assert watcher._multi_activity_waiter is old_waiter
        assert old_waiter.close_calls == 0
        assert waiters[1].close_calls == 1
        deadline = time.monotonic() + 3.0
        while strategy.start_calls < 2 and time.monotonic() < deadline:
            time.sleep(0.001)
        assert strategy.start_calls >= 2
        make_queue("replace.a").write("still-live")
        assert handled.wait(timeout=2.0)
        assert drive.is_alive()
    finally:
        watcher.stop()
        drive.join(timeout=2.0)


def test_background_post_replace_publication_failure_restores_old_waiter(
    broker_env,
    monkeypatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """Publication failure restores strategy ownership before request failure."""
    del thread_exception_guard
    db_path, make_queue = broker_env
    handled = threading.Event()
    waiters: list[FakeWaiter] = []

    class PublishFailWatcher(MultiQueueWatcher):
        fail_publication = True
        start_calls = 0

        def _start_strategy(self) -> None:
            self.start_calls += 1
            super()._start_strategy()

        def _publish_topology_locked(self, **kwargs) -> None:
            if self.fail_publication:
                self.fail_publication = False
                raise RuntimeError("injected publication failure")
            super()._publish_topology_locked(**kwargs)

    def create_waiter(_queues, *, stop_event):
        del stop_event
        waiter: FakeWaiter = BlockingWaiter() if not waiters else FakeWaiter()
        waiters.append(waiter)
        return waiter

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        create_waiter,
    )
    watcher = PublishFailWatcher(
        queue_configs={"publish.a": {"handler": lambda *_args: handled.set()}},
        db=db_path,
        inactive_probe_interval=0.01,
    )
    generation = watcher._queue_generation
    old_waiter = waiters[0]
    assert isinstance(old_waiter, BlockingWaiter)
    drive = watcher.run_in_thread()
    assert old_waiter.wait_entered.wait(timeout=2.0)
    caught: list[BaseException] = []

    def mutate() -> None:
        try:
            watcher.add_queue("publish.c", lambda *_args: None)
        except BaseException as exc:  # pragma: no cover - asserted below
            caught.append(exc)

    mutator = threading.Thread(target=mutate)
    mutator.start()
    old_waiter.release.set()
    mutator.join(timeout=2.0)
    try:
        assert len(caught) == 1
        assert str(caught[0]) == "injected publication failure"
        assert watcher.list_queues() == ["publish.a"]
        assert watcher._queue_generation == generation
        assert watcher._multi_activity_waiter is old_waiter
        assert watcher._strategy.uses_native_activity() is True
        assert old_waiter.close_calls == 0
        assert waiters[1].close_calls == 1
        deadline = time.monotonic() + 3.0
        while watcher.start_calls < 2 and time.monotonic() < deadline:
            time.sleep(0.001)
        assert watcher.start_calls >= 2
        make_queue("publish.a").write("still-live")
        assert handled.wait(timeout=2.0)
    finally:
        watcher.stop()
        drive.join(timeout=2.0)


def test_background_mutation_racing_stop_never_binds_after_owner_close(
    broker_env,
    monkeypatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """Stop-first rejects in-flight and queued mutations before replacement."""
    del thread_exception_guard
    db_path, _make_queue = broker_env
    factory_entered = threading.Event()
    factory_release = threading.Event()
    old_waiter = BlockingWaiter()
    candidate = FakeWaiter()
    calls = 0

    def create_waiter(_queues, *, stop_event):
        nonlocal calls
        del stop_event
        calls += 1
        if calls == 1:
            return old_waiter
        factory_entered.set()
        assert factory_release.wait(timeout=2.0)
        return candidate

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        create_waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"stop-race.a": {"handler": lambda *_args: None}},
        db=db_path,
    )
    drive = watcher.run_in_thread()
    assert old_waiter.wait_entered.wait(timeout=2.0)
    errors: list[BaseException] = []

    def add(name: str) -> None:
        try:
            watcher.add_queue(name, lambda *_args: None)
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    first = threading.Thread(target=add, args=("stop-race.c",))
    first.start()
    old_waiter.release.set()
    assert factory_entered.wait(timeout=2.0)
    second = threading.Thread(target=add, args=("stop-race.d",))
    second.start()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        with watcher._topology_lock:
            if watcher._topology_mutations:
                break
        time.sleep(0.001)
    else:
        raise AssertionError("queued stop-race mutation was not observed")

    watcher.stop(join=False)
    assert watcher._topology_stopping is True
    assert watcher._stop_event.is_set()
    assert old_waiter.close_calls == 0
    factory_release.set()
    first.join(timeout=2.0)
    second.join(timeout=2.0)
    drive.join(timeout=2.0)

    assert not first.is_alive() and not second.is_alive() and not drive.is_alive()
    assert len(errors) == 2
    assert all(isinstance(error, RuntimeError) for error in errors)
    assert watcher.list_queues() == ["stop-race.a"]
    assert candidate.close_calls == 1
    assert old_waiter.close_calls == 1
    assert old_waiter.close_threads == [drive.ident]
    assert watcher._strategy.uses_native_activity() is False


@pytest.mark.parametrize("previously_driven", [False, True])
def test_mutation_after_stop_is_rejected_before_effects(
    broker_env,
    monkeypatch,
    previously_driven: bool,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """Stopped watchers reject both mutators without queue or waiter effects."""
    del thread_exception_guard
    db_path, _make_queue = broker_env
    factory_calls = 0

    def create_waiter(_queues, *, stop_event):
        nonlocal factory_calls
        del stop_event
        factory_calls += 1
        return FakeWaiter()

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        create_waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"stopped.a": {"handler": lambda *_args: None}},
        db=db_path,
    )
    drive: threading.Thread | None = None
    if previously_driven:
        drive = watcher.run_in_thread()
    watcher.stop()
    if drive is not None:
        drive.join(timeout=2.0)

    baseline = (
        watcher.list_queues(),
        watcher._queue_generation,
        watcher._multi_activity_waiter,
        factory_calls,
    )
    with pytest.raises(RuntimeError, match="stopping"):
        watcher.add_queue("stopped.c", lambda *_args: None)
    with pytest.raises(RuntimeError, match="stopping"):
        watcher.remove_queue("stopped.a")
    assert (
        watcher.list_queues(),
        watcher._queue_generation,
        watcher._multi_activity_waiter,
        factory_calls,
    ) == baseline


def test_background_mutation_committed_before_stop_returns_success(
    broker_env,
    monkeypatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """A commit holding the serialization lock linearizes before public stop."""
    del thread_exception_guard
    db_path, _make_queue = broker_env
    stop_event = threading.Event()
    replace_entered = threading.Event()
    replace_release = threading.Event()
    stop_returned = threading.Event()
    waiters: list[BlockingWaiter] = []

    class GatedReplaceStrategy(PollingStrategy):
        def replace_activity_waiter(self, activity_waiter):
            replace_entered.set()
            assert replace_release.wait(timeout=2.0)
            return super().replace_activity_waiter(activity_waiter)

    def create_waiter(_queues, *, stop_event):
        del stop_event
        waiter = BlockingWaiter()
        waiters.append(waiter)
        return waiter

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        create_waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"commit.a": {"handler": lambda *_args: None}},
        db=db_path,
        stop_event=stop_event,
        polling_strategy=GatedReplaceStrategy(stop_event),
    )
    drive = watcher.run_in_thread()
    assert waiters[0].wait_entered.wait(timeout=2.0)
    mutation_errors: list[BaseException] = []

    def mutate() -> None:
        try:
            watcher.add_queue("commit.c", lambda *_args: None)
        except BaseException as exc:  # pragma: no cover - asserted below
            mutation_errors.append(exc)

    mutator = threading.Thread(target=mutate)
    mutator.start()
    waiters[0].release.set()
    assert replace_entered.wait(timeout=2.0)

    def stop() -> None:
        watcher.stop()
        stop_returned.set()

    stopper = threading.Thread(target=stop)
    stopper.start()
    assert not stop_returned.wait(timeout=0.05)
    assert watcher._topology_stopping is False
    replace_release.set()
    mutator.join(timeout=2.0)
    stopper.join(timeout=2.0)
    drive.join(timeout=2.0)

    assert mutation_errors == []
    assert watcher.list_queues() == ["commit.a", "commit.c"]
    assert stop_returned.is_set()
    assert not drive.is_alive()
    assert waiters[-1].close_calls == 1
    assert waiters[-1].close_threads == [drive.ident]


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
def test_waiter_close_failure_is_logged_once_and_not_retried(
    broker_env,
    monkeypatch,
    caplog,
    error_type: type[Exception],
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """A failed one-way close is logged but never retried on later stop calls."""
    del thread_exception_guard
    db_path, _make_queue = broker_env
    initial = BlockingWaiter()
    replacement = RaisingCloseWaiter(error_type)
    waiters = iter((initial, replacement))
    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        lambda _queues, *, stop_event: next(waiters),
    )
    watcher = MultiQueueWatcher(
        queue_configs={"close.a": {"handler": lambda *_args: None}},
        db=db_path,
    )
    drive = watcher.run_in_thread()
    assert initial.wait_entered.wait(timeout=2.0)
    mutator = threading.Thread(
        target=lambda: watcher.add_queue("close.c", lambda *_args: None)
    )
    mutator.start()
    initial.release.set()
    mutator.join(timeout=2.0)
    assert not mutator.is_alive()
    replacement.release.set()

    with caplog.at_level("DEBUG"):
        watcher.stop()
        drive.join(timeout=2.0)
        watcher.stop()

    assert replacement.close_calls == 1
    assert replacement.close_threads == [drive.ident]
    assert (
        sum(
            "Failed to close multi-queue activity waiter" in record.message
            for record in caplog.records
        )
        == 1
    )


def test_no_owner_stop_closes_attached_cached_waiter_once(
    broker_env,
    monkeypatch,
) -> None:
    """A legal no-drive attached cache is detached before inherited cleanup."""
    db_path, _make_queue = broker_env
    waiter = RaisingCloseWaiter(ValueError)
    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        lambda _queues, *, stop_event: waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"no-owner.a": {"handler": lambda *_args: None}},
        db=db_path,
    )
    watcher._start_strategy()
    assert watcher._strategy.uses_native_activity() is True

    watcher.stop()
    watcher.stop()

    assert waiter.close_calls == 1
    assert waiter.close_threads == [threading.get_ident()]
    assert watcher._multi_activity_waiter is None
    assert watcher._multi_activity_waiter_generation is None
    assert watcher._multi_activity_waiter_signature is None
    assert watcher._strategy.uses_native_activity() is False


def test_background_remove_last_queue_uses_empty_waiter_shortcut_then_add_restores_native(
    broker_env,
    monkeypatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """Empty membership uses polling without invoking the waiter factory."""
    del thread_exception_guard
    db_path, make_queue = broker_env
    handled = threading.Event()
    calls: list[tuple[str, ...]] = []
    waiters: list[BlockingWaiter] = []

    def create_waiter(queues, *, stop_event):
        del stop_event
        signature = tuple(queue.name for queue in queues)
        assert signature
        calls.append(signature)
        waiter = BlockingWaiter()
        waiters.append(waiter)
        return waiter

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        create_waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"empty.a": {"handler": lambda *_args: None}},
        db=db_path,
        inactive_probe_interval=0.01,
    )
    drive = watcher.run_in_thread()
    assert waiters[0].wait_entered.wait(timeout=2.0)
    remove = threading.Thread(target=lambda: watcher.remove_queue("empty.a"))
    remove.start()
    waiters[0].release.set()
    remove.join(timeout=2.0)
    assert not remove.is_alive()
    assert watcher.list_queues() == []
    assert watcher._multi_activity_waiter_signature == ()
    assert watcher._strategy.uses_native_activity() is False
    assert calls == [("empty.a",)]
    assert waiters[0].close_threads == [drive.ident]

    watcher.add_queue("empty.b", lambda *_args: handled.set())
    make_queue("empty.b").write("work")
    waiters[-1].release.set()
    try:
        assert calls[-1] == ("empty.b",)
        assert watcher._strategy.uses_native_activity() is True
        assert handled.wait(timeout=2.0)
    finally:
        waiters[-1].release.set()
        watcher.stop()
        drive.join(timeout=2.0)


def test_manual_wait_excludes_drive_start_mutation_and_second_manual_wait(
    broker_env,
    monkeypatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """One direct wait temporarily excludes all other watcher ownership."""
    del thread_exception_guard
    db_path, make_queue = broker_env
    handled = threading.Event()
    waiter = BlockingWaiter()
    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        lambda _queues, *, stop_event: waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"manual.a": {"handler": lambda *_args: handled.set()}},
        db=db_path,
        inactive_probe_interval=0.01,
    )
    manual = threading.Thread(target=lambda: watcher.wait_for_activity(0.5))
    manual.start()
    assert waiter.wait_entered.wait(timeout=2.0)
    try:
        assert watcher._topology_manual_wait_thread is manual
        with pytest.raises(RuntimeError, match="manual wait"):
            watcher.run_in_thread()
        with pytest.raises(RuntimeError, match="manual wait"):
            watcher.run()
        with pytest.raises(RuntimeError, match="manual wait"):
            watcher.add_queue("manual.c", lambda *_args: None)
        with pytest.raises(RuntimeError, match="manual wait"):
            watcher.remove_queue("manual.a")
        with pytest.raises(RuntimeError, match="manual wait"):
            watcher.wait_for_activity(0.01)
        assert waiter.close_calls == 0
    finally:
        waiter.release.set()
        manual.join(timeout=2.0)

    assert not manual.is_alive()
    assert watcher._topology_manual_wait_thread is None
    drive = watcher.run_in_thread()
    make_queue("manual.a").write("work")
    try:
        assert handled.wait(timeout=2.0)
    finally:
        watcher.stop()
        drive.join(timeout=2.0)


def test_manual_wait_nonpositive_and_stopped_calls_have_no_effects(
    broker_env,
    monkeypatch,
) -> None:
    """Non-positive and stopped direct waits return before waiter ownership."""
    db_path, _make_queue = broker_env
    waiter = FakeWaiter()
    factory_calls = 0

    def create_waiter(_queues, *, stop_event):
        nonlocal factory_calls
        del stop_event
        factory_calls += 1
        return waiter

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        create_waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"manual-noop.a": {"handler": lambda *_args: None}},
        db=db_path,
    )
    constructor_calls = factory_calls
    watcher.wait_for_activity(None)
    watcher.wait_for_activity(0.0)
    watcher.wait_for_activity(-1.0)
    assert factory_calls == constructor_calls
    assert waiter.wait_calls == []
    assert watcher._topology_manual_wait_thread is None

    watcher.stop()
    watcher.wait_for_activity(0.1)
    assert factory_calls == constructor_calls
    assert waiter.wait_calls == []
    assert watcher._topology_manual_wait_thread is None


def test_stop_during_manual_wait_leaves_close_to_manual_owner(
    broker_env,
    monkeypatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """Concurrent stop signals a manual wait but never closes across threads."""
    del thread_exception_guard
    db_path, _make_queue = broker_env
    waiter = BlockingWaiter()
    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        lambda _queues, *, stop_event: waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"manual-stop.a": {"handler": lambda *_args: None}},
        db=db_path,
    )
    watcher._start_strategy()
    manual = threading.Thread(target=lambda: watcher.wait_for_activity(1.0))
    manual.start()
    assert waiter.wait_entered.wait(timeout=2.0)

    watcher.stop()
    assert watcher._stop_event.is_set()
    assert watcher._topology_manual_wait_thread is manual
    assert waiter.close_calls == 0
    assert watcher._strategy.uses_native_activity() is True

    waiter.release.set()
    manual.join(timeout=2.0)
    assert not manual.is_alive()
    assert watcher._topology_manual_wait_thread is None
    assert waiter.close_calls == 1
    assert waiter.close_threads == [manual.ident]
    assert watcher._strategy.uses_native_activity() is False
    watcher.stop()
    assert waiter.close_calls == 1


@pytest.mark.parametrize("entry", ["run", "run_in_thread"])
def test_drive_start_after_public_stop_is_rejected_before_thread_creation(
    broker_env,
    entry: str,
) -> None:
    """A stopped watcher cannot reserve or claim a new drive."""
    db_path, _make_queue = broker_env
    watcher = MultiQueueWatcher(
        queue_configs={"restart.a": {"handler": lambda *_args: None}},
        db=db_path,
    )
    watcher.stop()
    baseline = (
        watcher._topology_owner_thread,
        watcher._topology_reserved_thread,
        watcher._thread,
    )

    with pytest.raises(RuntimeError, match="stopped"):
        if entry == "run":
            watcher.run()
        else:
            watcher.run_in_thread()
    assert (
        watcher._topology_owner_thread,
        watcher._topology_reserved_thread,
        watcher._thread,
    ) == baseline


def test_synchronous_run_registers_and_clears_drive_thread_for_stop(
    broker_env,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """Direct run publishes its owner weakref and clears it before returning."""
    del thread_exception_guard
    db_path, _make_queue = broker_env
    run_returned = threading.Event()
    release_outer_thread = threading.Event()
    watcher = MultiQueueWatcher(
        queue_configs={"sync.a": {"handler": lambda *_args: None}},
        db=db_path,
    )

    def run() -> None:
        watcher.run()
        run_returned.set()
        assert release_outer_thread.wait(timeout=2.0)

    outer = threading.Thread(target=run)
    outer.start()
    deadline = time.monotonic() + 2.0
    while watcher._topology_owner_thread is None and time.monotonic() < deadline:
        time.sleep(0.001)
    assert watcher._topology_owner_thread is outer
    assert watcher._thread is not None and watcher._thread() is outer

    watcher.add_queue("sync.c", lambda *_args: None)
    watcher.stop(timeout=0.05)
    assert run_returned.wait(timeout=2.0)
    assert watcher._topology_owner_thread is None
    assert watcher._thread is None
    assert outer.is_alive()

    watcher.stop(timeout=0.05)
    assert outer.is_alive()
    release_outer_thread.set()
    outer.join(timeout=2.0)
    assert not outer.is_alive()


def test_owner_fatal_exit_signals_every_queued_mutator(
    broker_env,
    monkeypatch,
) -> None:
    """A fatal owner exit releases every synchronous topology caller."""
    db_path, _make_queue = broker_env
    factory_entered = threading.Event()
    raise_fatal = threading.Event()
    fatal_caught = threading.Event()
    old_waiter = BlockingWaiter()
    calls = 0

    class FatalMutation(BaseException):
        pass

    class FatalCatchingWatcher(MultiQueueWatcher):
        def run_forever(self) -> None:
            try:
                super().run_forever()
            except FatalMutation:
                fatal_caught.set()

    def create_waiter(_queues, *, stop_event):
        nonlocal calls
        del stop_event
        calls += 1
        if calls == 1:
            return old_waiter
        factory_entered.set()
        assert raise_fatal.wait(timeout=2.0)
        raise FatalMutation

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        create_waiter,
    )
    watcher = FatalCatchingWatcher(
        queue_configs={"fatal.a": {"handler": lambda *_args: None}},
        db=db_path,
    )
    drive = watcher.run_in_thread()
    assert old_waiter.wait_entered.wait(timeout=2.0)
    errors: list[BaseException] = []

    def add(name: str) -> None:
        try:
            watcher.add_queue(name, lambda *_args: None)
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    mutators = [
        threading.Thread(target=add, args=(f"fatal.{suffix}",))
        for suffix in ("c", "d", "e")
    ]
    mutators[0].start()
    old_waiter.release.set()
    assert factory_entered.wait(timeout=2.0)
    for mutator in mutators[1:]:
        mutator.start()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        with watcher._topology_lock:
            if len(watcher._topology_mutations) == 2:
                break
        time.sleep(0.001)
    else:
        raise AssertionError("fatal-exit mutations were not queued")

    raise_fatal.set()
    for mutator in mutators:
        mutator.join(timeout=2.0)
    drive.join(timeout=2.0)

    assert fatal_caught.is_set()
    assert all(not mutator.is_alive() for mutator in mutators)
    assert len(errors) == 3
    assert all(isinstance(error, RuntimeError) for error in errors)
    assert watcher._topology_inflight is None
    assert not watcher._topology_mutations
    assert watcher._topology_owner_thread is None
    assert watcher._thread is None
    assert old_waiter.close_calls == 1
    assert old_waiter.close_threads == [drive.ident]


def test_same_waiter_replacement_publication_failure_preserves_installed_owner(
    broker_env,
    monkeypatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """A same-object replacement failure never closes pre-existing ownership."""
    del thread_exception_guard
    db_path, _make_queue = broker_env
    waiter = BlockingWaiter()

    class PublishFailWatcher(MultiQueueWatcher):
        fail_publication = True

        def _publish_topology_locked(self, **kwargs) -> None:
            if self.fail_publication:
                self.fail_publication = False
                raise RuntimeError("same-object publication failure")
            super()._publish_topology_locked(**kwargs)

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        lambda _queues, *, stop_event: waiter,
    )
    watcher = PublishFailWatcher(
        queue_configs={"same.a": {"handler": lambda *_args: None}},
        db=db_path,
    )
    drive = watcher.run_in_thread()
    assert waiter.wait_entered.wait(timeout=2.0)
    caught: list[BaseException] = []

    def mutate() -> None:
        try:
            watcher.add_queue("same.c", lambda *_args: None)
        except BaseException as exc:  # pragma: no cover - asserted below
            caught.append(exc)

    mutator = threading.Thread(target=mutate)
    mutator.start()
    waiter.release.set()
    mutator.join(timeout=2.0)
    try:
        assert len(caught) == 1
        assert str(caught[0]) == "same-object publication failure"
        assert watcher.list_queues() == ["same.a"]
        assert watcher._multi_activity_waiter is waiter
        assert watcher._strategy.uses_native_activity() is True
        assert waiter.close_calls == 0
    finally:
        watcher.stop()
        drive.join(timeout=2.0)
    assert waiter.close_calls == 1


@pytest.mark.parametrize("probe_point", ["replace", "close"])
def test_main_thread_sigint_after_waiter_replace_finishes_consistent_commit(
    probe_point: str,
) -> None:
    """SIGINT after replacement is delivered only after consistent publication."""
    if not hasattr(__import__("signal"), "raise_signal"):
        pytest.skip("signal.raise_signal is unavailable")
    env = dict(os.environ)
    env["WEFT_SIGINT_PROBE_POINT"] = probe_point
    completed = subprocess.run(
        [sys.executable, "-m", "tests.helpers.multiqueue_sigint_probe"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10.0,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    if "skipped" in result:
        pytest.skip(result["skipped"])
    assert result["consistent_at_interrupt"] is True
    assert result["mutation_done"] is True
    assert result["mutation_errors"] == []
    assert result["mutator_alive"] is False
    assert result["queues"] == ["sigint.a", "sigint.b", "sigint.c"]
    assert result["signatures"] == [
        ["sigint.a", "sigint.b"],
        ["sigint.a", "sigint.b", "sigint.c"],
    ]
    assert result["close_calls"] == [1, 1]
    assert result["close_threads"] == [
        [result["main_thread"]],
        [result["main_thread"]],
    ]


def test_stop_join_timeout_does_not_block_on_owner_finalization_lock(
    broker_env,
    monkeypatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """A bounded public join returns while owner-only close is still blocked."""
    del thread_exception_guard
    db_path, _make_queue = broker_env
    close_entered = threading.Event()
    close_release = threading.Event()
    stop_returned = threading.Event()

    class CloseBlockingWaiter(BlockingWaiter):
        def close(self) -> None:
            close_entered.set()
            assert close_release.wait(timeout=2.0)
            super().close()

    waiter = CloseBlockingWaiter()
    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        lambda _queues, *, stop_event: waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"timeout.a": {"handler": lambda *_args: None}},
        db=db_path,
    )
    drive = watcher.run_in_thread()
    assert waiter.wait_entered.wait(timeout=2.0)
    waiter.release.set()

    def stop() -> None:
        watcher.stop(timeout=0.01)
        stop_returned.set()

    stopper = threading.Thread(target=stop)
    stopper.start()
    assert close_entered.wait(timeout=2.0)
    assert stop_returned.wait(timeout=1.0)
    assert drive.is_alive()

    close_release.set()
    stopper.join(timeout=2.0)
    drive.join(timeout=2.0)
    assert not stopper.is_alive() and not drive.is_alive()
    assert watcher._topology_owner_thread is None
    assert watcher._thread is None


def test_postgres_background_dynamic_membership_rebinds_native_waiter(
    broker_env,
    monkeypatch,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """Real LISTEN/NOTIFY follows added and removed queue membership."""
    if active_test_backend() != POSTGRES_TEST_BACKEND:
        pytest.skip("requires BROKER_TEST_BACKEND=postgres")
    del thread_exception_guard
    db_path, make_queue = broker_env

    class RecordingProxy:
        def __init__(self, signature: tuple[str, ...], delegate) -> None:
            self.signature = signature
            self.delegate = delegate
            self.true_count = 0
            self.close_calls = 0

        def wait(self, timeout: float | None) -> bool:
            result = self.delegate.wait(timeout)
            if result:
                self.true_count += 1
            return result

        def close(self) -> None:
            self.close_calls += 1
            self.delegate.close()

    proxies: dict[tuple[str, ...], RecordingProxy] = {}

    def create_proxy(queues, *, stop_event):
        queue_list = list(queues)
        signature = tuple(queue.name for queue in queue_list)
        delegate = real_create_activity_waiter(
            queue_list,
            stop_event=stop_event,
        )
        assert delegate is not None
        proxy = RecordingProxy(signature, delegate)
        proxies[signature] = proxy
        return proxy

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        create_proxy,
    )
    c_first = threading.Event()
    c_second = threading.Event()
    b_handled = threading.Event()
    handler_errors: list[str] = []
    c_calls = 0

    def handle_c(*_args) -> None:
        nonlocal c_calls
        c_calls += 1
        expected = (
            ("pg-dynamic.a", "pg-dynamic.b", "pg-dynamic.c")
            if c_calls == 1
            else ("pg-dynamic.a", "pg-dynamic.c")
        )
        proxy = proxies.get(expected)
        if proxy is None or proxy.true_count == 0:
            handler_errors.append(f"no native wake recorded for {expected!r}")
        (c_first if c_calls == 1 else c_second).set()

    watcher = MultiQueueWatcher(
        queue_configs={
            "pg-dynamic.a": {"handler": lambda *_args: None},
            "pg-dynamic.b": {"handler": lambda *_args: b_handled.set()},
        },
        db=db_path,
        inactive_probe_interval=60.0,
    )
    writer_b = make_queue("pg-dynamic.b")
    writer_c = make_queue("pg-dynamic.c")
    drive = watcher.run_in_thread()
    try:
        watcher.add_queue("pg-dynamic.c", handle_c)
        writer_c.write("first")
        assert c_first.wait(timeout=3.0)
        assert handler_errors == []

        watcher.remove_queue("pg-dynamic.b")
        remaining = proxies[("pg-dynamic.a", "pg-dynamic.c")]
        quiet_baseline = remaining.true_count
        writer_b.write("removed")
        time.sleep(0.2)
        assert remaining.true_count == quiet_baseline
        assert not b_handled.is_set()

        writer_c.write("second")
        assert c_second.wait(timeout=3.0)
        assert handler_errors == []
        assert remaining.true_count > quiet_baseline
    finally:
        watcher.stop()
        drive.join(timeout=3.0)
        writer_b.close()
        writer_c.close()


def test_background_mutation_from_handler_is_rejected_before_effects(
    broker_env,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """Dispatch callbacks cannot synchronously mutate their own drive topology."""
    del thread_exception_guard
    db_path, make_queue = broker_env
    queue_name = "dynamic.handler"
    handled = threading.Event()
    handler_errors: list[BaseException] = []
    watcher: MultiQueueWatcher

    def handler(*_args) -> None:
        try:
            watcher.add_queue("dynamic.forbidden", lambda *_inner: None)
        except BaseException as exc:  # pragma: no cover - asserted below
            handler_errors.append(exc)
        finally:
            handled.set()

    watcher = MultiQueueWatcher(
        queue_configs={queue_name: {"handler": handler}},
        db=db_path,
        inactive_probe_interval=0.01,
    )
    generation = watcher._queue_generation
    signature = watcher._multi_activity_waiter_signature
    drive = watcher.run_in_thread()
    make_queue(queue_name).write("work")
    try:
        assert handled.wait(timeout=2.0)
        assert len(handler_errors) == 1
        assert isinstance(handler_errors[0], RuntimeError)
        assert watcher.list_queues() == [queue_name]
        assert watcher._queue_generation == generation
        assert watcher._multi_activity_waiter_signature == signature
        assert watcher.get_queue("dynamic.forbidden") is None
    finally:
        watcher.stop()
        drive.join(timeout=2.0)


@pytest.mark.parametrize("mutation", ["duplicate_add", "missing_remove"])
def test_background_membership_errors_return_to_requesting_thread(
    broker_env,
    mutation: str,
    thread_exception_guard: list[threading.ExceptHookArgs],
) -> None:
    """Expected membership errors fail only their synchronous request."""
    del thread_exception_guard
    db_path, make_queue = broker_env
    handled = threading.Event()
    watcher = MultiQueueWatcher(
        queue_configs={
            "errors.a": {"handler": lambda *_args: handled.set()},
        },
        db=db_path,
        inactive_probe_interval=0.01,
    )
    drive = watcher.run_in_thread()
    caught: list[BaseException] = []

    def mutate() -> None:
        try:
            if mutation == "duplicate_add":
                watcher.add_queue("errors.a", lambda *_args: None)
            else:
                watcher.remove_queue("errors.missing")
        except BaseException as exc:  # pragma: no cover - asserted below
            caught.append(exc)

    mutator = threading.Thread(target=mutate)
    mutator.start()
    mutator.join(timeout=2.0)
    make_queue("errors.a").write("still-live")
    try:
        assert not mutator.is_alive()
        assert len(caught) == 1
        assert isinstance(caught[0], ValueError)
        assert handled.wait(timeout=2.0)
        assert drive.is_alive()
    finally:
        watcher.stop()
        drive.join(timeout=2.0)


def test_peek_mode_ack_removes_message(broker_env) -> None:
    """Control queue (peek) handlers should see message and remove only when acked."""
    db_path, make_queue = broker_env
    queue_name = "T123.ctrl_in"
    queue = make_queue(queue_name)
    queue.write("STOP")

    seen: list[tuple[str, QueueMode]] = []

    def handler(message: str, timestamp: int, context: QueueMessageContext) -> None:
        seen.append((message, context.mode))
        context.queue.delete(message_id=context.timestamp)

    watcher = MultiQueueWatcher(
        queue_configs={
            queue_name: {
                "handler": handler,
                "mode": QueueMode.PEEK,
            },
        },
        db=db_path,
    )

    run_single_drain(watcher)

    assert seen == [("STOP", QueueMode.PEEK)]
    assert queue.peek_one() is None


def test_wait_for_activity_uses_simplebroker_multi_queue_waiter(
    broker_env,
    monkeypatch,
) -> None:
    db_path, _make_queue = broker_env
    received: dict[str, object] = {}
    fake_waiter = FakeWaiter()
    stop_event = threading.Event()

    def handler(
        _message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        raise AssertionError("wait_for_activity must not drain messages")

    def fake_create(queues, *, stop_event):
        received["queues"] = queues
        received["stop_event"] = stop_event
        return fake_waiter

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        fake_create,
    )

    watcher = MultiQueueWatcher(
        queue_configs={
            "wait.one": {"handler": handler},
            "wait.two": {"handler": handler},
        },
        db=db_path,
        stop_event=stop_event,
    )

    try:
        watcher.wait_for_activity(timeout=0.25)
    finally:
        watcher.stop(join=False)

    assert [queue.name for queue in received["queues"]] == ["wait.one", "wait.two"]
    assert received["stop_event"] is stop_event
    assert fake_waiter.wait_calls == [0.25]


def test_start_strategy_uses_multi_queue_activity_waiter(
    broker_env,
    monkeypatch,
) -> None:
    db_path, _make_queue = broker_env
    received: dict[str, object] = {}
    fake_waiter = FakeWaiter()

    def handler(
        _message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        pass

    def fake_create(queues, *, stop_event):
        received["queues"] = queues
        received["stop_event"] = stop_event
        return fake_waiter

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        fake_create,
    )

    watcher = MultiQueueWatcher(
        queue_configs={
            "strategy.one": {"handler": handler},
            "strategy.two": {"handler": handler},
        },
        db=db_path,
    )

    try:
        watcher._start_strategy()

        assert [queue.name for queue in received["queues"]] == [
            "strategy.one",
            "strategy.two",
        ]
        assert watcher._strategy.uses_native_activity() is True
        assert fake_waiter.close_calls == 0
    finally:
        watcher.stop(join=False)


def test_reset_multi_activity_waiter_detaches_strategy_waiter(
    broker_env,
    monkeypatch,
) -> None:
    db_path, _make_queue = broker_env
    fake_waiter = FakeWaiter()

    def handler(
        _message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        pass

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        lambda queues, *, stop_event: fake_waiter,
    )

    watcher = MultiQueueWatcher(
        queue_configs={"reset.one": {"handler": handler}},
        db=db_path,
    )

    try:
        watcher._start_strategy()
        assert watcher._strategy.uses_native_activity() is True

        watcher._reset_multi_activity_waiter()

        assert fake_waiter.close_calls == 1
        assert watcher._strategy.uses_native_activity() is False
    finally:
        watcher.stop(join=False)


def test_wait_for_activity_positive_timeout_uses_waiter_without_precheck(
    broker_env,
    monkeypatch,
) -> None:
    db_path, make_queue = broker_env
    queue = make_queue("pending.one")
    queue.write("ready")
    create_calls = 0
    fake_waiter = FakeWaiter()

    def handler(
        _message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        raise AssertionError("wait_for_activity must not drain messages")

    def fake_create(queues, *, stop_event):
        nonlocal create_calls
        del queues, stop_event
        create_calls += 1
        return fake_waiter

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        fake_create,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"pending.one": {"handler": handler}},
        db=db_path,
    )

    try:
        watcher.wait_for_activity(timeout=0.25)
    finally:
        watcher.stop(join=False)

    assert create_calls == 1
    assert fake_waiter.wait_calls == [0.25]
    assert queue.peek_one() == "ready"


def test_wait_for_activity_zero_timeout_does_not_probe_queues(
    broker_env,
    monkeypatch,
) -> None:
    db_path, _make_queue = broker_env

    def handler(
        _message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        pass

    watcher = MultiQueueWatcher(
        queue_configs={"zero-timeout.one": {"handler": handler}},
        db=db_path,
    )
    monkeypatch.setattr(
        watcher,
        "_has_pending_messages",
        lambda: pytest.fail("zero-timeout waits must not poll watched queues"),
    )

    try:
        watcher.wait_for_activity(timeout=0)
    finally:
        watcher.stop(join=False)


def test_native_waiter_activity_forces_inactive_queue_probe(
    broker_env,
    monkeypatch,
) -> None:
    db_path, make_queue = broker_env
    queue = make_queue("native-precheck.two")
    queue.write("ready")
    seen: list[str] = []
    fake_waiter = FakeWaiter(result=True)

    def handler(
        message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        seen.append(message)

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        lambda queues, *, stop_event: fake_waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={
            "native-precheck.one": {"handler": handler},
            "native-precheck.two": {"handler": handler},
        },
        db=db_path,
        check_interval=10,
    )

    try:
        watcher._next_inactive_probe_at = time.monotonic() + 60
        watcher._check_counter = 1
        watcher.wait_for_activity(timeout=0.25)
        watcher._drain_queue()
    finally:
        watcher.stop(join=False)

    assert seen == ["ready"]
    assert fake_waiter.wait_calls == [0.25]


def test_native_waiter_timeout_does_not_probe_inactive_queues_before_deadline(
    broker_env,
    monkeypatch,
) -> None:
    db_path, _make_queue = broker_env
    fake_waiter = FakeWaiter(result=False)

    def handler(
        _message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        pass

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        lambda queues, *, stop_event: fake_waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"native-timeout.one": {"handler": handler}},
        db=db_path,
        check_interval=10,
    )
    monkeypatch.setattr(
        watcher,
        "_queue_has_pending",
        lambda _queue: pytest.fail(
            "native waiter timeout must not become an inactive queue probe"
        ),
    )

    try:
        watcher._next_inactive_probe_at = time.monotonic() + 60
        watcher._check_counter = 1
        watcher.wait_for_activity(timeout=0.01)
        watcher._drain_queue()
    finally:
        watcher.stop(join=False)

    assert fake_waiter.wait_calls == [0.01]


def test_inactive_queue_discovery_is_time_bounded(
    broker_env,
) -> None:
    db_path, make_queue = broker_env
    queue = make_queue("periodic-discovery.two")
    queue.write("ready")
    seen: list[str] = []

    def handler(
        message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        seen.append(message)

    watcher = MultiQueueWatcher(
        queue_configs={
            "periodic-discovery.one": {"handler": handler},
            "periodic-discovery.two": {"handler": handler},
        },
        db=db_path,
        check_interval=10,
    )

    try:
        watcher._check_counter = 1
        watcher._next_inactive_probe_at = time.monotonic() + 60
        watcher._drain_queue()
        assert seen == []

        watcher._next_inactive_probe_at = time.monotonic() - 1
        watcher._drain_queue()
    finally:
        watcher.stop(join=False)

    assert seen == ["ready"]


def test_wait_for_activity_falls_back_when_helper_returns_none(
    broker_env,
    monkeypatch,
) -> None:
    db_path, _make_queue = broker_env

    def handler(
        _message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        pass

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        lambda queues, *, stop_event: None,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"fallback.one": {"handler": handler}},
        db=db_path,
    )

    try:
        start = time.monotonic()
        watcher.wait_for_activity(timeout=0.01)
    finally:
        watcher.stop(join=False)

    assert time.monotonic() - start >= 0


def test_queue_set_changes_close_stale_multi_queue_waiter(
    broker_env,
    monkeypatch,
) -> None:
    db_path, _make_queue = broker_env
    waiters: list[FakeWaiter] = []

    def handler(
        _message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        pass

    def fake_create(queues, *, stop_event):
        del queues, stop_event
        waiter = FakeWaiter()
        waiters.append(waiter)
        return waiter

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        fake_create,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"dynamic.one": {"handler": handler}},
        db=db_path,
    )

    try:
        watcher.wait_for_activity(timeout=0.01)
        watcher.add_queue("dynamic.two", handler)
        watcher.wait_for_activity(timeout=0.01)
    finally:
        watcher.stop(join=False)

    assert len(waiters) == 2
    assert waiters[0].close_calls == 1
    assert waiters[1].close_calls == 1


def test_stop_closes_multi_queue_waiter(
    broker_env,
    monkeypatch,
) -> None:
    db_path, _make_queue = broker_env
    fake_waiter = FakeWaiter()

    def handler(
        _message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        pass

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        lambda queues, *, stop_event: fake_waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"stop.one": {"handler": handler}},
        db=db_path,
    )

    watcher.wait_for_activity(timeout=0.01)
    watcher.stop(join=False)

    assert fake_waiter.close_calls == 1


def test_wait_for_activity_falls_back_when_waiter_raises(
    broker_env,
    monkeypatch,
) -> None:
    db_path, _make_queue = broker_env
    fake_waiter = FakeWaiter(raises=True)

    def handler(
        _message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        pass

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        lambda queues, *, stop_event: fake_waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={"raise.one": {"handler": handler}},
        db=db_path,
    )

    try:
        watcher.wait_for_activity(timeout=0.01)
    finally:
        watcher.stop(join=False)

    assert fake_waiter.wait_calls == [0.01]
    assert fake_waiter.close_calls == 1


def test_background_watcher_path_uses_multi_queue_waiter(
    broker_env,
    monkeypatch,
) -> None:
    db_path, _make_queue = broker_env
    fake_waiter = FakeWaiter()

    def handler(
        _message: str,
        _timestamp: int,
        _context: QueueMessageContext,
    ) -> None:
        pass

    monkeypatch.setattr(
        "weft.core.tasks.multiqueue_watcher.create_activity_waiter_for_queues",
        lambda queues, *, stop_event: fake_waiter,
    )
    watcher = MultiQueueWatcher(
        queue_configs={
            "thread.one": {"handler": handler},
            "thread.two": {"handler": handler},
        },
        db=db_path,
    )

    thread = watcher.run_in_thread()
    try:
        assert fake_waiter.wait_entered.wait(timeout=5.0)
        assert fake_waiter.wait_calls
    finally:
        watcher.stop(join=True)
        thread.join(timeout=1.0)


def test_priority_queue_drains_before_lower_priority(broker_env) -> None:
    db_path, make_queue = broker_env
    internal_name = "priority.internal"
    public_name = "priority.public"
    internal_reserved_name = "priority.internal.reserved"
    public_reserved_name = "priority.public.reserved"
    internal = make_queue(internal_name)
    public = make_queue(public_name)
    internal_reserved = make_queue(internal_reserved_name)
    public_reserved = make_queue(public_reserved_name)

    for body in ["internal-1", "internal-2", "internal-3"]:
        internal.write(body)
    for body in ["public-1", "public-2"]:
        public.write(body)

    seen: list[tuple[str, str]] = []

    def handler(message: str, timestamp: int, context: QueueMessageContext) -> None:
        seen.append((context.queue_name, message))
        assert context.reserved_queue_name is not None
        make_queue(context.reserved_queue_name).delete(message_id=timestamp)

    watcher = MultiQueueWatcher(
        queue_configs={
            internal_name: {
                "handler": handler,
                "mode": QueueMode.RESERVE,
                "reserved_queue": internal_reserved_name,
                "priority": QUEUE_PRIORITY_INTERNAL,
            },
            public_name: {
                "handler": handler,
                "mode": QueueMode.RESERVE,
                "reserved_queue": public_reserved_name,
                "priority": QUEUE_PRIORITY_NORMAL,
            },
        },
        db=db_path,
    )

    try:
        run_single_drain(watcher)
    finally:
        watcher.stop(join=False)

    assert seen[:3] == [
        (internal_name, "internal-1"),
        (internal_name, "internal-2"),
        (internal_name, "internal-3"),
    ]
    assert seen[3:] == [(public_name, "public-1")]
    assert internal.peek_one() is None
    assert internal_reserved.peek_one() is None
    assert public_reserved.peek_one() is None
    assert public.peek_one() == "public-2"


def test_equal_priority_preserves_existing_round_robin_behavior(broker_env) -> None:
    db_path, make_queue = broker_env
    first_name = "priority.equal.first"
    second_name = "priority.equal.second"
    first_reserved_name = "priority.equal.first.reserved"
    second_reserved_name = "priority.equal.second.reserved"
    make_queue(first_name).write("first-1")
    make_queue(first_name).write("first-2")
    make_queue(second_name).write("second-1")
    make_queue(second_name).write("second-2")

    seen: list[tuple[str, str]] = []

    def handler(message: str, timestamp: int, context: QueueMessageContext) -> None:
        seen.append((context.queue_name, message))
        assert context.reserved_queue_name is not None
        make_queue(context.reserved_queue_name).delete(message_id=timestamp)

    watcher = MultiQueueWatcher(
        queue_configs={
            first_name: {
                "handler": handler,
                "mode": QueueMode.RESERVE,
                "reserved_queue": first_reserved_name,
            },
            second_name: {
                "handler": handler,
                "mode": QueueMode.RESERVE,
                "reserved_queue": second_reserved_name,
            },
        },
        db=db_path,
    )

    try:
        run_single_drain(watcher)
    finally:
        watcher.stop(join=False)

    assert seen == [
        (first_name, "first-1"),
        (second_name, "second-1"),
    ]
    assert make_queue(first_name).peek_one() == "first-2"
    assert make_queue(second_name).peek_one() == "second-2"


def test_priority_drain_stops_when_high_priority_queue_is_empty(broker_env) -> None:
    db_path, make_queue = broker_env
    internal_name = "priority.empty.internal"
    public_name = "priority.empty.public"
    internal_reserved_name = "priority.empty.internal.reserved"
    public_reserved_name = "priority.empty.public.reserved"
    public = make_queue(public_name)
    public.write("public-1")
    public.write("public-2")

    seen: list[tuple[str, str]] = []

    def handler(message: str, timestamp: int, context: QueueMessageContext) -> None:
        seen.append((context.queue_name, message))
        assert context.reserved_queue_name is not None
        make_queue(context.reserved_queue_name).delete(message_id=timestamp)

    watcher = MultiQueueWatcher(
        queue_configs={
            internal_name: {
                "handler": handler,
                "mode": QueueMode.RESERVE,
                "reserved_queue": internal_reserved_name,
                "priority": QUEUE_PRIORITY_INTERNAL,
            },
            public_name: {
                "handler": handler,
                "mode": QueueMode.RESERVE,
                "reserved_queue": public_reserved_name,
                "priority": QUEUE_PRIORITY_NORMAL,
            },
        },
        db=db_path,
    )

    try:
        run_single_drain(watcher)
    finally:
        watcher.stop(join=False)

    assert seen == [(public_name, "public-1")]
    assert public.peek_one() == "public-2"


def test_peek_mode_without_ack_leaves_message(broker_env) -> None:
    """Peek mode should leave messages available if handler skips ack."""
    db_path, make_queue = broker_env
    queue_name = "T124.ctrl_in"
    queue = make_queue(queue_name)
    queue.write("PAUSE")

    def handler(message: str, timestamp: int, context: QueueMessageContext) -> None:
        # Intentionally do not ack
        assert context.mode == QueueMode.PEEK

    watcher = MultiQueueWatcher(
        queue_configs={
            queue_name: {
                "handler": handler,
                "mode": QueueMode.PEEK,
            },
        },
        db=db_path,
    )

    run_single_drain(watcher)

    # Message should still be present because handler didn't ack
    peeked = queue.peek_one(with_timestamps=True)
    assert peeked is not None
    assert peeked[0] == "PAUSE"


def test_reserve_mode_moves_then_ack_clears_reserved(broker_env) -> None:
    """Reserve mode should move messages before handler and allow ack from reserved queue."""
    db_path, make_queue = broker_env
    inbox_name = "T125.inbox"
    reserved_name = "T125.reserved"
    inbox = make_queue(inbox_name)
    reserved = make_queue(reserved_name)
    inbox.write("job-1")

    seen: list[tuple[str, QueueMode]] = []

    def handler(message: str, timestamp: int, context: QueueMessageContext) -> None:
        seen.append((message, context.mode))
        # Message should have been moved to the reserved queue
        reserved_peek = reserved.peek_one(with_timestamps=True)
        assert reserved_peek is not None
        assert reserved_peek[0] == message
        assert reserved_peek[1] == timestamp
        reserved.delete(message_id=timestamp)

    watcher = MultiQueueWatcher(
        queue_configs={
            inbox_name: {
                "handler": handler,
                "mode": QueueMode.RESERVE,
                "reserved_queue": reserved_name,
            }
        },
        db=db_path,
    )

    run_single_drain(watcher)

    assert seen == [("job-1", QueueMode.RESERVE)]
    # Inbox should be empty after move
    assert inbox.peek_one() is None
    # Reserved queue should also be empty after handler acked the message
    assert reserved.peek_one() is None


def test_read_mode_consumes_message_without_ack(broker_env) -> None:
    """Read mode should consume messages immediately, ack becomes a no-op."""
    db_path, make_queue = broker_env
    queue_name = "T126.custom"
    queue = make_queue(queue_name)
    queue.write("payload")

    def handler(message: str, timestamp: int, context: QueueMessageContext) -> None:
        assert context.mode == QueueMode.READ
        # Even without ack, message should already be gone

    watcher = MultiQueueWatcher(
        queue_configs={
            queue_name: {
                "handler": handler,
                "mode": QueueMode.READ,
            },
        },
        db=db_path,
    )

    run_single_drain(watcher)

    assert queue.peek_one() is None
