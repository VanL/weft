"""Versioned task-owned observation values [MF-5]."""

from __future__ import annotations

from typing import Any

import pytest

from weft.core.task_evidence import (
    observation_config_from_taskspec_payload,
    parse_task_observation,
    parse_task_projection,
)

pytestmark = [pytest.mark.shared]


def test_observation_accepts_custom_pipeline_routes() -> None:
    wire = {
        "version": 1,
        "mode": "pipeline",
        "stream_output": True,
        "routes": {
            "inbox": "custom.in",
            "outbox": "custom.out",
            "ctrl_in": "custom.ctrl",
            "ctrl_out": "custom.done",
            "pipeline_status": "custom.status",
        },
    }
    config = parse_task_observation(wire)
    assert config is not None
    assert config.mode == "pipeline"
    assert config.pipeline_status == "custom.status"
    assert config.to_payload() == wire


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"stream_output": 1},
        {"extra": "ignored"},
        {"routes": {"outbox": "only"}},
    ],
)
def test_observation_rejects_unsupported_or_incomplete_configuration(
    changes: dict[str, Any],
) -> None:
    wire = {
        "version": 1,
        "mode": "one_shot",
        "stream_output": False,
        "routes": {
            "inbox": "i",
            "outbox": "o",
            "ctrl_in": "ci",
            "ctrl_out": "co",
            "pipeline_status": None,
        },
    }
    assert parse_task_observation({**wire, **changes}) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"started_at": False},
        {"completed_at": 1},
        {"error": ""},
        {"unknown": "ignored"},
    ],
)
def test_projection_rejects_invalid_terminal_contract(changes: dict[str, Any]) -> None:
    projection = {
        "version": 1,
        "status": "completed",
        "event": "work_completed",
        "started_at": 2,
        "metadata": {},
        "completed_at": 3,
    }
    assert parse_task_projection({**projection, **changes}, terminal=True) is None


def test_taskspec_configuration_accepts_unrelated_redaction_and_requires_pipeline_status() -> (
    None
):
    payload = {
        "tid": "123",
        "spec": {
            "persistent": False,
            "interactive": False,
            "stream_output": True,
            "env": "[REDACTED]",
        },
        "metadata": {
            "role": "pipeline",
            "_weft_pipeline_runtime": {"queues": {"status": "custom.pipeline.status"}},
        },
        "io": {
            "inputs": {"inbox": "i"},
            "outputs": {"outbox": "o"},
            "control": {"ctrl_in": "ci", "ctrl_out": "co"},
        },
    }
    # The runtime metadata spelling is a public wire key, asserted independently.
    config = observation_config_from_taskspec_payload(payload)
    assert config is not None
    assert config.pipeline_status == "custom.pipeline.status"
    payload["metadata"] = {"role": "pipeline"}
    assert observation_config_from_taskspec_payload(payload) is None
    payload["metadata"] = "[REDACTED]"
    assert observation_config_from_taskspec_payload(payload) is None


def test_discovery_configuration_rejects_redacted_required_route_and_malformed_role() -> (
    None
):
    payload: dict[str, Any] = {
        "tid": "123",
        "spec": {"persistent": False, "interactive": False, "stream_output": False},
        "metadata": {},
        "io": {
            "inputs": {"inbox": "i"},
            "outputs": {"outbox": "[REDACTED]"},
            "control": {"ctrl_in": "ci", "ctrl_out": "co"},
        },
    }
    assert observation_config_from_taskspec_payload(payload) is None
    payload["io"]["outputs"]["outbox"] = "actual-output"
    payload["metadata"] = {"role": {"bad": "shape"}}
    assert observation_config_from_taskspec_payload(payload) is None
