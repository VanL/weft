"""Tests for the standalone manager launcher script."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Self

import pytest

from simplebroker.ext import BrokerError
from weft._constants import SERVICE_OWNER_SCHEMA

pytestmark = [pytest.mark.shared]


def _load_launch_manager_module() -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "bin" / "launch_manager.py"
    spec = importlib.util.spec_from_file_location(
        "weft_launch_manager_script",
        script_path,
    )
    if spec is None or spec.loader is None:
        raise AssertionError(f"Unable to load launch_manager.py: {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeQueue:
    def __init__(self, events: list[object]) -> None:
        self._events = events
        self.closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def peek_many(self, *, limit: int, with_timestamps: bool) -> object:
        assert limit == 1000
        assert with_timestamps is True
        event = self._events.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event


class _FakeContext:
    broker_target = "ignored"

    def __init__(self) -> None:
        self.broker_config: dict[str, Any] = {}
        self.session_exited = False

    @contextmanager
    def session(self) -> Iterator[None]:
        try:
            yield
        finally:
            self.session_exited = True


@pytest.mark.parametrize(
    "read_error",
    [
        BrokerError("broker read failed"),
        OSError("storage read failed"),
        RuntimeError("backend read failed"),
    ],
)
def test_wait_for_registry_retries_supported_read_errors(
    read_error: Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launch_manager = _load_launch_manager_module()
    tid = "123"
    active_record: dict[str, Any] = {
        "schema": SERVICE_OWNER_SCHEMA,
        "service_key": "manager:weft.spawn.requests:file:/tmp/weft.db",
        "service_type": "manager",
        "owner_tid": tid,
        "name": "manager",
        "status": "active",
        "queues": {},
        "runtime_handle": {},
        "metadata": {},
        "role": "manager",
        "capabilities": [],
    }
    queue = _FakeQueue(
        [
            read_error,
            [(json.dumps(active_record), 1)],
        ]
    )
    monkeypatch.setattr(launch_manager, "Queue", lambda *_args, **_kwargs: queue)
    monkeypatch.setattr(
        launch_manager,
        "time",
        SimpleNamespace(time=lambda: 0.0, sleep=lambda _delay: None),
    )
    context = _FakeContext()

    result = launch_manager._wait_for_registry(context, tid)

    assert result == active_record
    assert queue.closed
    assert context.session_exited


def test_wait_for_registry_propagates_unexpected_read_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launch_manager = _load_launch_manager_module()
    queue = _FakeQueue([TypeError("invalid queue result")])
    monkeypatch.setattr(launch_manager, "Queue", lambda *_args, **_kwargs: queue)
    monkeypatch.setattr(
        launch_manager,
        "time",
        SimpleNamespace(time=lambda: 0.0, sleep=lambda _delay: None),
    )
    context = _FakeContext()

    with pytest.raises(TypeError, match="invalid queue result"):
        launch_manager._wait_for_registry(context, "123")
    assert queue.closed
    assert context.session_exited
