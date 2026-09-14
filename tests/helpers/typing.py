"""Shared callable contracts for broker-backed fixtures (Spec: [TS-0]).

Spec: docs/specifications/08-Testing_Strategy.md [TS-0], [TS-3].
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from simplebroker import BrokerTarget, Queue
from weft.core.tasks import Consumer
from weft.core.taskspec import TaskSpec

BrokerEnv = tuple[BrokerTarget, Callable[[str], Queue]]


class QueueFactory(Protocol):
    """Create a queue owned by the calling test's harness."""

    def __call__(self, name: str, *, persistent: bool = True) -> Queue: ...


class TaskFactory(Protocol):
    """Create a consumer whose teardown belongs to the fixture."""

    def __call__(self, taskspec: TaskSpec) -> Consumer: ...


def record_and_return[T, R](records: list[T], item: T, result: R) -> R:
    """Record a spy observation and return its configured callback result."""
    records.append(item)
    return result
