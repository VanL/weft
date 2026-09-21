"""Subprocess probe for SIGINT during dynamic activity-waiter replacement."""

from __future__ import annotations

import json
import os
import signal
import tempfile
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from simplebroker import Queue
from simplebroker.ext import ActivityWaiter, PollingStrategy
from weft.core.tasks import multiqueue_watcher as watcher_module
from weft.core.tasks.multiqueue_watcher import MultiQueueWatcher


class RecordingWaiter:
    """Small protocol waiter that records wait and close ownership."""

    def __init__(self, *, interrupt_on_close: bool = False) -> None:
        self.wait_entered = threading.Event()
        self.close_calls = 0
        self.close_threads: list[int] = []
        self.interrupt_on_close = interrupt_on_close

    def wait(self, timeout: float | None) -> bool:
        self.wait_entered.set()
        time.sleep(min(0.01, timeout or 0.01))
        return False

    def close(self) -> None:
        self.close_calls += 1
        self.close_threads.append(threading.get_ident())
        if self.interrupt_on_close:
            self.interrupt_on_close = False
            signal.raise_signal(signal.SIGINT)


class InterruptingStrategy(PollingStrategy):
    """Raise SIGINT once, immediately after a real replacement succeeds."""

    def __init__(
        self,
        stop_event: threading.Event,
        *,
        interrupt_on_replace: bool,
        held_lock: str | None = None,
    ) -> None:
        super().__init__(stop_event)
        self.interrupt_next_replace = interrupt_on_replace
        self.held_lock = held_lock
        self.notification_lock = threading.Lock()

    def replace_activity_waiter(
        self, activity_waiter: ActivityWaiter | None
    ) -> ActivityWaiter | None:
        displaced = super().replace_activity_waiter(activity_waiter)
        if self.interrupt_next_replace:
            self.interrupt_next_replace = False
            if self.held_lock == "stop_event":
                # Reproduce a signal interrupting Event.set/wait on its own lock.
                condition = cast(threading.Condition, vars(self._stop_event)["_cond"])
                with condition:
                    signal.raise_signal(signal.SIGINT)
                    signal.raise_signal(signal.SIGINT)
            elif self.held_lock == "notification":
                with self.notification_lock:
                    signal.raise_signal(signal.SIGINT)
                    signal.raise_signal(signal.SIGINT)
            else:
                signal.raise_signal(signal.SIGINT)
        return displaced

    def notify_activity(self) -> None:
        """Model an embedder strategy whose notification takes a lock."""
        with self.notification_lock:
            super().notify_activity()


def main() -> int:
    """Run the main-thread watcher probe and print one JSON result."""
    if not hasattr(signal, "SIGINT") or not hasattr(signal, "raise_signal"):
        print(json.dumps({"skipped": "SIGINT raise_signal unavailable"}))
        return 0

    probe_point = os.environ.get("WEFT_SIGINT_PROBE_POINT", "replace")
    assert probe_point in {"replace", "close", "stop_event", "notification"}
    stop_event = threading.Event()
    strategy = InterruptingStrategy(
        stop_event,
        interrupt_on_replace=probe_point != "close",
        held_lock=probe_point
        if probe_point in {"stop_event", "notification"}
        else None,
    )
    waiters: list[RecordingWaiter] = []
    signatures: list[tuple[str, ...]] = []

    def create_waiter(
        queues: Sequence[Queue], *, stop_event: threading.Event | None = None
    ) -> RecordingWaiter:
        del stop_event
        signatures.append(tuple(queue.name for queue in queues))
        waiter = RecordingWaiter(
            interrupt_on_close=probe_point == "close" and not waiters
        )
        waiters.append(waiter)
        return waiter

    watcher_module.create_activity_waiter_for_queues = create_waiter
    with tempfile.TemporaryDirectory() as directory:
        watcher = MultiQueueWatcher(
            queue_configs={
                "sigint.a": {"handler": lambda *_args: None},
                "sigint.b": {"handler": lambda *_args: None},
            },
            db=Path(directory) / "broker.db",
            stop_event=stop_event,
            polling_strategy=strategy,
        )
        mutation_done = threading.Event()
        mutation_errors: list[str] = []

        def mutate() -> None:
            # The parent subprocess deadline owns liveness for this probe. The
            # handoff itself is synchronization, not a two-second SLA.
            waiters[0].wait_entered.wait()
            try:
                watcher.add_queue("sigint.c", lambda *_args: None)
            except BaseException as exc:  # noqa: BLE001 approved [TS-3.1] [RUFF-SUP-359] exception
                mutation_errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                mutation_done.set()

        mutator = threading.Thread(target=mutate)
        mutator.start()
        interrupt_after_request = False
        consistent_at_interrupt = False
        try:
            watcher.run()
        except KeyboardInterrupt:
            interrupt_after_request = mutation_done.is_set()
            consistent_at_interrupt = (
                watcher._topology_inflight is None
                and watcher.list_queues() == ["sigint.a", "sigint.b", "sigint.c"]
            )
        mutator.join(timeout=2.0)

        result = {
            "interrupt_after_request": interrupt_after_request,
            "consistent_at_interrupt": consistent_at_interrupt,
            "mutation_done": mutation_done.is_set(),
            "mutation_errors": mutation_errors,
            "mutator_alive": mutator.is_alive(),
            "queues": watcher.list_queues(),
            "signatures": signatures,
            "close_calls": [waiter.close_calls for waiter in waiters],
            "close_threads": [waiter.close_threads for waiter in waiters],
            "main_thread": threading.get_ident(),
        }
        print(json.dumps(result, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
