"""Spec checks for spawn request durability (WA-2)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from tests.helpers.test_backend import prepare_project_root
from weft.commands import run as run_module
from weft.context import build_context
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec

pytestmark = [pytest.mark.shared]


def _build_spec(tid: str) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="spawn-retry",
        spec=SpecSection(
            type="function", function_target="tests.tasks.sample_targets:echo_payload"
        ),
        io=IOSection(
            outputs={"outbox": f"T{tid}.outbox"},
            control={
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        ),
        state=StateSection(),
    )


def test_spawn_request_uses_retry(monkeypatch, tmp_path) -> None:
    root = prepare_project_root(tmp_path)
    context = build_context(spec_context=root)
    tid = str(time.time_ns())
    taskspec = _build_spec(tid)
    called = {"count": 0}
    original_broker = type(context).broker

    @contextmanager
    def _broker_with_retry_probe(self) -> Iterator[Any]:
        with original_broker(self) as broker:
            original = broker._run_with_retry

            class BrokerProxy:
                def __init__(self, wrapped: Any) -> None:
                    self._wrapped = wrapped

                def _run_with_retry(self, fn, *args, **kwargs):
                    called["count"] += 1
                    return original(fn, *args, **kwargs)

                def __getattr__(self, name: str) -> Any:
                    return getattr(self._wrapped, name)

            yield BrokerProxy(broker)

    monkeypatch.setattr(type(context), "broker", _broker_with_retry_probe)

    run_module._enqueue_taskspec(context, taskspec, None)

    assert called["count"] >= 1
