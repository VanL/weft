"""Task-owned snapshot fixtures for observer contract tests."""

from __future__ import annotations

import json
from typing import Any

from weft._constants import TERMINAL_TASK_STATUSES
from weft.context import WeftContext
from weft.core.task_evidence import terminal_status_from_event
from weft.core.task_state import read_task_state_snapshot, task_state_queue_name
from weft.helpers import tid_short_form


def write_task_observation(ctx: WeftContext, event: dict[str, Any]) -> None:
    """Publish the current-view counterpart of a test lifecycle event."""
    if event.get("source") == "manager":
        return
    tid = event["tid"]
    taskspec = event.get("taskspec")
    if not isinstance(taskspec, dict):
        payload = _continue_task_observation(ctx, event)
        if payload is None:
            return
    else:
        state = taskspec.get("state", {})
        status = terminal_status_from_event(event) or state.get(
            "status", event.get("status", "unknown")
        )
        existing = read_task_state_snapshot(ctx, tid)
        if (
            existing is not None
            and existing[1].get("terminal")
            and status not in TERMINAL_TASK_STATUSES
        ):
            return
        projection = {
            "version": 1,
            "status": status,
            "event": event["event"],
            "started_at": state.get("started_at"),
            "metadata": taskspec.get("metadata", {}),
        }
        terminal = status in TERMINAL_TASK_STATUSES
        if terminal:
            projection.update(
                {
                    key: state[key]
                    for key in ("completed_at", "return_code", "error")
                    if state.get(key) is not None
                }
            )
        spec = taskspec.get("spec", {})
        io = taskspec.get("io", {})
        metadata = taskspec.get("metadata", {})
        pipeline = metadata.get("role") == "pipeline"
        routes = {
            "inbox": io.get("inputs", {}).get("inbox", f"T{tid}.inbox"),
            "outbox": io.get("outputs", {}).get("outbox", f"T{tid}.outbox"),
            "ctrl_in": io.get("control", {}).get("ctrl_in", f"T{tid}.ctrl_in"),
            "ctrl_out": io.get("control", {}).get("ctrl_out", f"T{tid}.ctrl_out"),
            "pipeline_status": metadata.get("_weft_pipeline_runtime", {})
            .get("queues", {})
            .get("status", f"P{tid}.status")
            if pipeline
            else None,
        }
        payload = {
            "full": tid,
            "short": tid_short_form(tid),
            "name": taskspec.get("name", tid),
            "runner": taskspec.get("spec", {}).get("runner", {}).get("name", "host"),
            "terminal": terminal,
            "projection": projection,
            "observation": {
                "version": 1,
                "mode": "pipeline"
                if pipeline
                else "persistent"
                if spec.get("persistent") or spec.get("interactive")
                else "one_shot",
                "stream_output": bool(spec.get("stream_output", False)),
                "routes": routes,
            },
        }
    if not payload.get("terminal"):
        for key in ("activity", "waiting_on"):
            if key in event:
                payload[key] = event[key]
    with ctx.queue(task_state_queue_name(tid), persistent=False) as queue:
        queue.write(json.dumps(payload))


def _continue_task_observation(
    ctx: WeftContext, event: dict[str, Any]
) -> dict[str, Any] | None:
    """Apply an activity/completion fixture to its already-published snapshot."""
    existing = read_task_state_snapshot(ctx, event["tid"])
    if existing is None or existing[1].get("terminal"):
        return None
    payload = existing[1]
    projection = payload["projection"]
    projection["event"] = event["event"]
    terminal_status = terminal_status_from_event(event)
    if terminal_status is not None:
        payload["terminal"] = True
        projection["status"] = terminal_status
        projection.update(
            {
                key: event[key]
                for key in ("completed_at", "return_code", "error")
                if event.get(key) is not None
            }
        )
        payload.pop("activity", None)
        payload.pop("waiting_on", None)
    return payload


def write_task_event(ctx: WeftContext, event: dict[str, Any]) -> None:
    """Publish both observation surfaces for a lifecycle fixture."""
    with ctx.queue("weft.log.tasks", persistent=False) as queue:
        queue.write(json.dumps(event))
    write_task_observation(ctx, event)


def task_observation_fields(ctx: WeftContext, tid: str) -> dict[str, Any]:
    """Retain the fixture's observation when adding scoped runtime evidence."""
    existing = read_task_state_snapshot(ctx, tid)
    return existing[1] if existing is not None else {}
