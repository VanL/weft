"""Spawn-portable producers that leak a terminal sender to a live grandchild.

A producer hands its private response sender to a spawned grandchild that
holds it open, records the grandchild pid by atomic rename, and exits with
``os._exit(0)`` (skipping multiprocessing's atexit join of non-daemon
children). The receiver then sees no EOF while the grandchild lives, so only
the producer's process sentinel can report the exit. Every target here is
module-level so the spawn start method can pickle it on POSIX and Windows.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.5]
"""

from __future__ import annotations

import os
import time
from multiprocessing import get_context
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, Final, NoReturn

import psutil

from weft.core.runners.agent_session_protocol import (
    make_booted_response,
    make_ready_response,
)
from weft.core.terminal_handoff_transport import send_terminal_payload

GRANDCHILD_HOLD_SECONDS: Final[float] = 30.0
"""How long the grandchild holds the leaked sender; tests kill it sooner."""

GRANDCHILD_READY_TIMEOUT_SECONDS: Final[float] = 10.0
"""Bound on the producer's wait for the grandchild to hold the sender."""


def hold_sender(response_sender: Connection, ready_event: Any) -> None:
    """Hold a duplicate of the sender open, after signalling that it is held."""

    del response_sender  # the duplicate stays open in this process until exit
    ready_event.set()
    time.sleep(GRANDCHILD_HOLD_SECONDS)


def _leak_sender_to_grandchild(response_sender: Connection, pidfile: str) -> None:
    ctx = get_context("spawn")
    ready_event = ctx.Event()
    grandchild = ctx.Process(target=hold_sender, args=(response_sender, ready_event))
    grandchild.start()
    if not ready_event.wait(GRANDCHILD_READY_TIMEOUT_SECONDS):
        os._exit(3)
    pending = Path(f"{pidfile}.pending")
    pending.write_text(str(grandchild.pid), encoding="utf-8")
    pending.replace(pidfile)


def silent_leaking_producer(response_sender: Connection, pidfile: str) -> NoReturn:
    """Leak the sender to a grandchild, send nothing, and exit."""

    _leak_sender_to_grandchild(response_sender, pidfile)
    os._exit(0)


def leaking_session_producer(
    pidfile: str,
    request_queue: Any,
    response_sender: Connection,
) -> NoReturn:
    """Agent-session producer that leaks its sender, takes one item, and exits.

    The grandchild is started before ``ready`` so that the work item's
    supervision measures the drain, not interpreter start-up.
    """

    send_terminal_payload(response_sender, make_booted_response())
    _leak_sender_to_grandchild(response_sender, pidfile)
    send_terminal_payload(response_sender, make_ready_response())
    request_queue.get()
    os._exit(0)


def wait_for_pidfile(pidfile: Path, *, timeout: float = 10.0) -> int:
    """Return the pid recorded by atomic rename, failing after ``timeout``."""

    deadline = time.monotonic() + timeout
    while not pidfile.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f"pidfile {pidfile} was not written")
        time.sleep(0.01)
    return int(pidfile.read_text(encoding="utf-8"))


def kill_pid(pid: int | None) -> None:
    """Kill and reap a grandchild, ignoring one that is already gone."""

    if pid is None:
        return
    try:
        process = psutil.Process(pid)
        process.kill()
        process.wait(timeout=5.0)
    except psutil.NoSuchProcess:
        pass
