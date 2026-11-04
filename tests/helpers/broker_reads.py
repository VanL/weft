"""Count broker-boundary calls that read or delete one named queue [QUEUE.7].

The counter wraps methods on the SimpleBroker broker class found from a live
connection's MRO (the class whose ``vars()`` define ``peek_many``), so a
quiet-window test can prove that a reactor reads nothing. It counts, in the
counting process only, every call of a ``BrokerCore`` method that reads the
named queue given as its first argument, every call of a method that reads
every queue at once, and every named delete. It does not see reads made in
another process or through a method added to SimpleBroker after 8.7.2.

Spec references:
- docs/specifications/07-System_Invariants.md [QUEUE.7]
- docs/specifications/01-Core_Components.md [CC-2.1]
"""

from __future__ import annotations

from typing import Any, Final

import pytest

from simplebroker import Queue

NAMED_QUEUE_READ_METHODS: Final[tuple[str, ...]] = (
    "peek_one",
    "peek_many",
    "peek_generator",
    "claim_one",
    "claim_many",
    "claim_generator",
    "move_one",
    "move_many",
    "move_generator",
    "has_pending_messages",
    "latest_pending_timestamp",
    "queue_exists_and_has_messages",
    "get_queue_stat",
    "find_message_ids",
    "queue_exists",
)
"""Every SimpleBroker 8.7.2 ``BrokerCore`` method that reads one named queue."""

WHOLE_DATABASE_READ_METHODS: Final[tuple[str, ...]] = (
    "list_queues",
    "get_queue_stats",
    "list_queue_stats",
    "get_overall_stats",
    "count_claimed_messages",
    "status",
)
"""``BrokerCore`` methods that read every queue and name none of them."""

QUEUE_DELETE_METHODS: Final[tuple[str, ...]] = ("delete", "delete_message_ids")
"""``BrokerCore`` methods that delete rows of one named queue."""


def count_queue_calls(
    monkeypatch: pytest.MonkeyPatch, sample: Queue, queue_name: str
) -> dict[str, int]:
    """Wrap the broker class and return live counts for *queue_name*.

    Args:
        monkeypatch: Restores every wrapped method at test teardown.
        sample: Any queue on the target, used to find the broker class.
        queue_name: The queue whose named reads and deletes are counted.

    Returns:
        A dictionary updated in place with ``reads`` (named-queue reads of
        *queue_name*), ``whole_database_reads`` (every whole-database read)
        and ``deletes`` (named deletes of *queue_name*). Tests may reset the
        values to zero to start a window.
    """

    with sample.get_connection() as connection:
        broker_class = next(
            klass for klass in type(connection).__mro__ if "peek_many" in vars(klass)
        )
    counts = {"reads": 0, "whole_database_reads": 0, "deletes": 0}

    def wrap(name: str, kind: str, *, named: bool) -> None:
        original = getattr(broker_class, name)

        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            queue = args[0] if args else kwargs.get("queue")
            if not named or queue == queue_name:
                counts[kind] += 1
            return original(self, *args, **kwargs)

        monkeypatch.setattr(broker_class, name, wrapper)

    for name in NAMED_QUEUE_READ_METHODS:
        wrap(name, "reads", named=True)
    for name in WHOLE_DATABASE_READ_METHODS:
        wrap(name, "whole_database_reads", named=False)
    for name in QUEUE_DELETE_METHODS:
        wrap(name, "deletes", named=True)
    return counts
