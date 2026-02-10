"""Spec checks for spawn request durability (WA-2)."""

from __future__ import annotations

import time

from weft.commands import run as run_module
from weft.context import build_context
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec


def _build_spec(tid: str) -> TaskSpec:
    return TaskSpec(
        tid=tid,
        name="spawn-retry",
        spec=SpecSection(type="function", function_target="tests.tasks.sample_targets:echo_payload"),
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
    context = build_context(spec_context=tmp_path)
    tid = str(time.time_ns())
    taskspec = _build_spec(tid)
    called = {"count": 0}

    original = run_module.BrokerDB._run_with_retry

    def _patched(self, fn, *args, **kwargs):
        called["count"] += 1
        return original(self, fn, *args, **kwargs)

    monkeypatch.setattr(run_module.BrokerDB, "_run_with_retry", _patched)

    run_module._enqueue_taskspec(context, {}, taskspec, None)

    assert called["count"] >= 1
