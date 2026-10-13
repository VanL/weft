"""Real host parent/child progress (Spec: [MA-1.8], [MF-1], [MF-6])."""

from __future__ import annotations

from typing import Any

import pytest

from tests.helpers.weft_harness import WeftTestHarness
from weft._constants import WEFT_GLOBAL_LOG_QUEUE
from weft.client import WeftClient, connect
from weft.core.runtime_identity import get_runtime_identity
from weft.helpers import iter_queue_json_entries

pytestmark = [pytest.mark.shared]


def submit_leaf_from_host_worker(
    payload: Any, *, explicit_ancestry: bool = True
) -> dict[str, Any]:
    """Exercise context inheritance, retained submission, and an actual child wait."""
    identity = get_runtime_identity()
    if identity is None:
        raise RuntimeError("host worker has no runtime identity")
    with connect() as client:
        child = client.submit(
            {
                "name": "admission-leaf",
                "spec": {
                    "type": "function",
                    "function_target": "tests.tasks.sample_targets:echo_payload",
                },
                "metadata": (
                    {"parent_tid": "1777000000000000456"} if explicit_ancestry else {}
                ),
            },
            payload=payload,
        )
        result = child.result(timeout=30.0)
        return {
            "parent_tid": identity.tid,
            "child_tid": child.tid,
            "child_status": result.status,
            "value": result.value,
        }


@pytest.mark.timeout(90)
@pytest.mark.parametrize("explicit_ancestry", [False, True])
def test_host_parent_submits_leaf_and_waits_through_child_lane(
    weft_harness: WeftTestHarness,
    explicit_ancestry: bool,
) -> None:
    """Actual worker identity reaches Manager without replacing application ancestry."""
    weft_harness.ensure_foreground_manager()
    with WeftClient(context=weft_harness.context) as client:
        parent = client.submit(
            {
                "name": "admission-parent",
                "spec": {
                    "type": "function",
                    "function_target": (
                        "tests.system.test_child_admission:submit_leaf_from_host_worker"
                    ),
                    "timeout": 45.0,
                    "keyword_args": {"explicit_ancestry": explicit_ancestry},
                },
            },
            payload="leaf-completed",
        )
        weft_harness.register_tid(parent.tid)
        result = parent.result(timeout=60.0)
    assert result.status == "completed", result.error
    assert isinstance(result.value, dict)
    assert result.value["parent_tid"] == parent.tid
    assert result.value["child_status"] == "completed"
    assert result.value["value"] == "leaf-completed"
    child_tid = result.value["child_tid"]
    weft_harness.register_tid(child_tid)
    with weft_harness.context.queue(WEFT_GLOBAL_LOG_QUEUE, persistent=True) as log:
        events = [
            payload
            for payload, _timestamp in iter_queue_json_entries(log)
            if payload.get("event") == "task_spawned"
            and payload.get("child_tid") == child_tid
        ]
    assert len(events) == 1
    assert events[0]["submitter_tid"] == parent.tid
    assert events[0]["child_taskspec"]["metadata"]["parent_tid"] == (
        "1777000000000000456" if explicit_ancestry else parent.tid
    )
