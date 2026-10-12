"""History loans and read-only Monitor acquisition at command boundaries [MF-5]."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.commands import _task_history
from weft.core.monitor.store import MonitorStore, open_monitor_store

pytestmark = [pytest.mark.shared]


@pytest.mark.parametrize("empty_loan", [False, True])
def test_taskspec_history_uses_acquired_events_even_when_empty(
    weft_harness: WeftTestHarness,
    empty_loan: bool,
) -> None:
    """A fresh query would select contradictory retained history.

    The core reader has its own filtering coverage. This adapter must respect
    an acquired empty result and fold the newest specification in a loan.
    """
    context = weft_harness.context
    tid = "1777000000000000991"
    with context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as queue:
        queue.write(json.dumps({"tid": tid, "taskspec": {"name": "other-read"}}))
        events: list[tuple[dict[str, Any], int]] = []
        if not empty_loan:
            events = [
                ({"tid": tid, "taskspec": {"name": "first"}}, 1),
                ({"tid": tid, "taskspec": {"name": "latest"}}, 2),
                ({"tid": tid, "event": "completed"}, 3),
            ]
        with queue.get_connection() as broker:
            result = _task_history.load_latest_taskspec_payload(
                context,
                tid,
                broker=broker,
                task_events=events,
            )
    assert result == (None if empty_loan else {"name": "latest"})


@pytest.mark.parametrize("initialized", [False, True])
def test_monitor_record_absence_does_not_initialize_or_migrate(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    initialized: bool,
) -> None:
    """Both uninitialized and initialized absent stores are read-only absence."""
    context = weft_harness.context
    with context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as queue:
        with queue.get_connection():
            pass
        if initialized:
            open_monitor_store(context, queue=queue).ensure_schema()

        def reject_maintenance(_store: MonitorStore) -> None:
            raise AssertionError("observation attempted Monitor schema maintenance")

        monkeypatch.setattr(MonitorStore, "ensure_schema", reject_maintenance)
        record, error = _task_history.read_monitor_task_record(
            context, "1777000000000000992", queue=queue
        )
    assert record is None
    assert error is None


def test_monitor_record_failure_preserves_diagnostic_and_stack(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unavailable read must stay distinct from absence and log its cause."""
    failure = RuntimeError("retained Monitor read unavailable")

    def fail_read(_store: MonitorStore, _tid: str) -> None:
        raise failure

    monkeypatch.setattr(MonitorStore, "get_task", fail_read)
    with caplog.at_level(logging.DEBUG, logger=_task_history.__name__):
        record, error = _task_history.read_monitor_task_record(
            weft_harness.context, "1777000000000000993"
        )
    assert record is None
    assert error == str(failure)
    assert any(item.exc_info and item.exc_info[1] is failure for item in caplog.records)


def test_monitor_record_does_not_swallow_interruption(
    weft_harness: WeftTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fatal interruption must cross the acquisition boundary unchanged."""
    failure = KeyboardInterrupt("cancel Monitor observation")

    def interrupt_read(_store: MonitorStore, _tid: str) -> None:
        raise failure

    monkeypatch.setattr(MonitorStore, "get_task", interrupt_read)
    with pytest.raises(KeyboardInterrupt) as caught:
        _task_history.read_monitor_task_record(
            weft_harness.context, "1777000000000000994"
        )
    assert caught.value is failure
