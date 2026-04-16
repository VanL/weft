"""Runtime endpoint registration tests for BaseTask helpers.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.4.1]
- docs/specifications/05-Message_Flow_and_State.md [MF-3.1]
"""

from __future__ import annotations

import time

import pytest

from tests.tasks.test_task_execution import make_function_taskspec
from weft._constants import WEFT_ENDPOINTS_REGISTRY_QUEUE
from weft.core.tasks import Consumer
from weft.helpers import iter_queue_json_entries


def _entries(queue) -> list[dict[str, object]]:
    return [payload for payload, _message_id in iter_queue_json_entries(queue)]


@pytest.fixture
def unique_tid() -> str:
    return str(time.time_ns())


def test_task_can_register_and_unregister_named_endpoint(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)
    registry = make_queue(WEFT_ENDPOINTS_REGISTRY_QUEUE)

    try:
        task.register_endpoint_name(
            "mayor",
            metadata={"role": "operator-facing"},
        )
        records = _entries(registry)
        assert len(records) == 1
        assert records[0]["name"] == "mayor"
        assert records[0]["tid"] == unique_tid
        assert records[0]["inbox"] == spec.io.inputs["inbox"]
        assert records[0]["ctrl_in"] == spec.io.control["ctrl_in"]
        assert records[0]["metadata"] == {"role": "operator-facing"}

        task.unregister_endpoint_name()
        assert _entries(registry) == []
    finally:
        task.cleanup()
        registry.close()


def test_task_reregistration_replaces_prior_endpoint_claim(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)
    registry = make_queue(WEFT_ENDPOINTS_REGISTRY_QUEUE)

    try:
        task.register_endpoint_name("mayor")
        task.register_endpoint_name("supervisor.daily")

        records = _entries(registry)
        assert len(records) == 1
        assert records[0]["name"] == "supervisor.daily"
        assert records[0]["tid"] == unique_tid
    finally:
        task.cleanup()
        registry.close()


def test_task_endpoint_name_validation_rejects_invalid_names(
    broker_env,
    unique_tid: str,
) -> None:
    db_path, make_queue = broker_env
    spec = make_function_taskspec(unique_tid, "tests.tasks.sample_targets:echo_payload")
    task = Consumer(db_path, spec)
    registry = make_queue(WEFT_ENDPOINTS_REGISTRY_QUEUE)

    try:
        try:
            task.register_endpoint_name("bad name")
        except ValueError as exc:
            assert "endpoint name" in str(exc)
        else:  # pragma: no cover - guard
            raise AssertionError("register_endpoint_name should reject invalid names")

        assert _entries(registry) == []
    finally:
        task.cleanup()
        registry.close()
