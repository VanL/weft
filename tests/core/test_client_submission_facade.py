"""Retained client submission setup regressions (Spec: [PY-1], [SB-0.4])."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from simplebroker import BrokerSession, Queue
from tests.helpers.weft_harness import (
    DEFAULT_TASK_COMPLETION_TIMEOUT,
    WeftTestHarness,
)
from weft.client import Task, WeftClient
from weft.context import WeftContext

pytestmark = [pytest.mark.shared]


@pytest.fixture
def facade_type(weft_harness: WeftTestHarness) -> type[Any]:
    """Instrument the actual facade without importing broker implementation modules."""
    with weft_harness.context.queue("facade-test-probe", persistent=True) as queue:
        assert queue.conn is not None
        return type(queue.conn)


def test_retained_submissions_reuse_warm_facade_and_commit_distinct_work(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    facade_type: type[Any],
) -> None:
    """Warm submissions must not reconstruct backend validation facades.

    Counting session handles or physical SQLite connections misses the defect:
    fresh facades still repeat PostgreSQL discovery and schema inspection.
    Instrument the real dependency constructor, without replacing submission,
    queue operations, manager observation, or task execution.
    """

    weft_harness.ensure_foreground_manager()
    taskspec = {
        "name": "retained-facade",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
            "weft_context": str(weft_harness.root),
        },
    }
    owner_thread = threading.current_thread()
    constructed: list[Any] = []
    original_init = facade_type.__init__

    def counted_init(connection: Any, *args: Any, **kwargs: Any) -> None:
        original_init(connection, *args, **kwargs)
        # A foreground harness manager has an independent reactor thread.
        # Its startup and task-owned work are not client submission setup.
        if threading.current_thread() is owner_thread:
            constructed.append(connection)

    submitted: list[tuple[Task, str]] = []
    with WeftClient(context=weft_harness.context) as client:
        warm = client.submit(taskspec, payload="warm")
        weft_harness.register_tid(warm.tid)
        assert warm.result(timeout=DEFAULT_TASK_COMPLETION_TIMEOUT).value == "warm"

        with monkeypatch.context() as instrumentation:
            instrumentation.setattr(facade_type, "__init__", counted_init)
            for payload in ("second", "third"):
                task = client.submit(taskspec, payload=payload)
                weft_harness.register_tid(task.tid)
                submitted.append((task, payload))

        for task, payload in submitted:
            result = task.result(timeout=DEFAULT_TASK_COMPLETION_TIMEOUT)
            assert result.status == "completed"
            assert result.value == payload
        assert len({task.tid for task, _ in submitted}) == 2
        assert constructed == [], (
            "warm retained submissions reconstructed backend connection facades"
        )


def test_client_reentry_mints_a_new_owned_facade_and_commits_work(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second client scope must not borrow the first scope's closed facade.

    Existing re-entry coverage counts session handles. A stale facade can
    silently reacquire a broker while belonging to the previous minting scope,
    so count real queue minting as well as observing committed work.
    """

    weft_harness.ensure_foreground_manager()
    owner_thread = threading.current_thread()
    minted: list[tuple[BrokerSession, Queue]] = []
    original_queue = BrokerSession.queue

    def counted_queue(session: BrokerSession, name: str) -> Queue:
        queue = original_queue(session, name)
        if threading.current_thread() is owner_thread:
            minted.append((session, queue))
        return queue

    submitted: list[tuple[Task, str]] = []
    client = WeftClient(context=weft_harness.context)
    with monkeypatch.context() as instrumentation:
        instrumentation.setattr(BrokerSession, "queue", counted_queue)
        for payload in ("first-scope", "second-scope"):
            with client:
                task = client.submit(
                    {
                        "name": "facade-reentry",
                        "spec": {
                            "type": "function",
                            "function_target": "tests.tasks.sample_targets:echo_payload",
                            "weft_context": str(weft_harness.root),
                        },
                    },
                    payload=payload,
                )
                weft_harness.register_tid(task.tid)
                submitted.append((task, payload))

    for task, payload in submitted:
        result = task.result(timeout=DEFAULT_TASK_COMPLETION_TIMEOUT)
        assert result.status == "completed"
        assert result.value == payload
    assert len(minted) == 2
    assert minted[0][0] is not minted[1][0]
    assert minted[0][1] is not minted[1][1]
    assert minted[0][1].session is minted[0][0]
    assert minted[1][1].session is minted[1][0]


def test_first_submission_facade_failure_preserves_session_for_retry(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    facade_type: type[Any],
) -> None:
    """A failed facade constructor leaves session cleanup and retry owned.

    Inject one external setup failure at the real dependency constructor.
    Successful attempts still use the real broker, manager, and function worker.
    """

    weft_harness.ensure_foreground_manager()
    taskspec = {
        "name": "facade-retry",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
            "weft_context": str(weft_harness.root),
        },
    }
    owner_thread = threading.current_thread()
    sessions: list[BrokerSession] = []
    construction_attempts = 0
    original_session = WeftContext.session
    original_init = facade_type.__init__

    def counted_session(context: WeftContext) -> BrokerSession:
        session = original_session(context)
        if threading.current_thread() is owner_thread:
            sessions.append(session)
        return session

    def fail_first_init(connection: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal construction_attempts
        if threading.current_thread() is owner_thread:
            construction_attempts += 1
            if construction_attempts == 1:
                raise RuntimeError("facade setup refused")
        original_init(connection, *args, **kwargs)

    submitted: list[tuple[Task, str]] = []
    with WeftClient(context=weft_harness.context) as client:
        with monkeypatch.context() as instrumentation:
            instrumentation.setattr(WeftContext, "session", counted_session)
            instrumentation.setattr(facade_type, "__init__", fail_first_init)
            with pytest.raises(RuntimeError, match="facade setup refused"):
                client.submit(taskspec, payload="rejected-before-acceptance")
            for payload in ("retry", "next"):
                task = client.submit(taskspec, payload=payload)
                weft_harness.register_tid(task.tid)
                submitted.append((task, payload))

        for task, payload in submitted:
            result = task.result(timeout=DEFAULT_TASK_COMPLETION_TIMEOUT)
            assert result.status == "completed"
            assert result.value == payload
        assert len(sessions) == 1
        assert construction_attempts == 2


def test_retained_facade_recovers_after_public_thread_core_recycling(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retained facade must resolve the current core for each operation.

    Public recycle_thread() forces real cache replacement. This is a supported
    recovery boundary, not a simulation of arbitrary connection corruption.
    """

    weft_harness.ensure_foreground_manager()
    taskspec = {
        "name": "facade-core-recovery",
        "spec": {
            "type": "function",
            "function_target": "tests.tasks.sample_targets:echo_payload",
            "weft_context": str(weft_harness.root),
        },
    }
    owner_thread = threading.current_thread()
    minted: list[Queue] = []
    original_queue = BrokerSession.queue

    def counted_queue(session: BrokerSession, name: str) -> Queue:
        queue = original_queue(session, name)
        if threading.current_thread() is owner_thread:
            minted.append(queue)
        return queue

    with WeftClient(context=weft_harness.context) as client:
        with monkeypatch.context() as instrumentation:
            instrumentation.setattr(BrokerSession, "queue", counted_queue)
            first = client.submit(taskspec, payload="before-recycle")
            weft_harness.register_tid(first.tid)
            assert len(minted) == 1
            queue = minted[0]
            session = queue.session
            assert session is not None
            with queue.get_connection() as initial_core:
                pass
            session.recycle_thread()
            second = client.submit(taskspec, payload="after-recycle")
            weft_harness.register_tid(second.tid)
            with queue.get_connection() as replacement_core:
                assert replacement_core is not initial_core
            assert minted == [queue]

        for task, payload in (
            (first, "before-recycle"),
            (second, "after-recycle"),
        ):
            result = task.result(timeout=DEFAULT_TASK_COMPLETION_TIMEOUT)
            assert result.status == "completed"
            assert result.value == payload
