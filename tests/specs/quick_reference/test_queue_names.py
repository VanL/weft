"""Spec checks for global queue naming (Quick Reference)."""

from __future__ import annotations

from weft._constants import (
    WEFT_ENDPOINTS_REGISTRY_QUEUE,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_PIPELINES_STATE_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_STREAMING_SESSIONS_QUEUE,
    WEFT_TASK_STATE_QUEUE_PREFIX,
)


def test_global_queue_names_match_spec() -> None:
    assert WEFT_GLOBAL_LOG_QUEUE == "weft.log.tasks"
    assert WEFT_SPAWN_REQUESTS_QUEUE == "weft.spawn.requests"
    assert WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE == "weft.spawn.internal"
    assert WEFT_MANAGER_OUTBOX_QUEUE == "weft.manager.outbox"
    assert WEFT_SERVICES_REGISTRY_QUEUE == "weft.state.services"
    assert WEFT_TASK_STATE_QUEUE_PREFIX == "weft.state.tasks."
    assert WEFT_STREAMING_SESSIONS_QUEUE == "weft.state.streaming"
    assert WEFT_ENDPOINTS_REGISTRY_QUEUE == "weft.state.endpoints"
    assert WEFT_PIPELINES_STATE_QUEUE == "weft.state.pipelines"
