"""Spec checks for global queue naming (Quick Reference)."""

from __future__ import annotations

from weft._constants import (
    WEFT_ENDPOINTS_REGISTRY_QUEUE,
    WEFT_GLOBAL_LOG_QUEUE,
    WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE,
    WEFT_MANAGER_CTRL_IN_QUEUE,
    WEFT_MANAGER_CTRL_OUT_QUEUE,
    WEFT_MANAGER_OUTBOX_QUEUE,
    WEFT_PIPELINES_STATE_QUEUE,
    WEFT_SERVICES_REGISTRY_QUEUE,
    WEFT_SPAWN_REQUESTS_QUEUE,
    WEFT_STREAMING_SESSIONS_QUEUE,
    WEFT_TID_MAPPINGS_QUEUE,
)


def test_global_queue_names_match_spec() -> None:
    assert WEFT_GLOBAL_LOG_QUEUE == "weft.log.tasks"
    assert WEFT_SPAWN_REQUESTS_QUEUE == "weft.spawn.requests"
    assert WEFT_INTERNAL_SPAWN_REQUESTS_QUEUE == "weft.spawn.internal"
    assert WEFT_MANAGER_CTRL_IN_QUEUE == "weft.manager.ctrl_in"
    assert WEFT_MANAGER_CTRL_OUT_QUEUE == "weft.manager.ctrl_out"
    assert WEFT_MANAGER_OUTBOX_QUEUE == "weft.manager.outbox"
    assert WEFT_SERVICES_REGISTRY_QUEUE == "weft.state.services"
    assert WEFT_TID_MAPPINGS_QUEUE == "weft.state.tid_mappings"
    assert WEFT_STREAMING_SESSIONS_QUEUE == "weft.state.streaming"
    assert WEFT_ENDPOINTS_REGISTRY_QUEUE == "weft.state.endpoints"
    assert WEFT_PIPELINES_STATE_QUEUE == "weft.state.pipelines"
