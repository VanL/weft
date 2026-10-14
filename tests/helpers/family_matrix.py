"""Real nested workers for transparent participants and ancestry contraction."""

from __future__ import annotations

import json
import threading
from typing import Any

from tests.helpers.family_recovery import SnapshotFaultManager
from tests.helpers.family_workload import _family_event, _wait_release
from weft.client import connect


def chain_node(payload: dict[str, Any]) -> Any:
    """An intermediate node returns its child TID while its ancestor joins it."""
    depth = int(payload["depth"])
    _family_event("chain_started", depth)
    with connect() as client:
        if depth == 3:
            _wait_release(
                client.context, "test.chain.release", {"index": depth}, "chain_waiting"
            )
        child = (
            client.submit(
                {
                    "name": f"chain-{depth + 1}",
                    "spec": {
                        "type": "function",
                        "function_target": "tests.helpers.family_matrix:chain_node",
                    },
                },
                payload={"depth": depth + 1},
            )
            if depth < 3
            else client.submit(
                {
                    "name": "chain-leaf",
                    "spec": {
                        "type": "function",
                        "function_target": "tests.helpers.family_workload:family_leaf",
                    },
                },
                payload={"index": 99},
            )
        )
        _family_event("chain_child", depth, child_tid=child.tid)
        if depth == 2:
            return {"child_tid": child.tid}
        result = child.result(timeout=60.0)
        if result.status != "completed":
            raise RuntimeError(str(result.error))
        if depth == 1:
            assert isinstance(result.value, dict)
            result = client.task(result.value["child_tid"]).result(timeout=60.0)
            if result.status != "completed":
                raise RuntimeError(str(result.error))
        return result.value


def sibling_rendezvous_root(_payload: dict[str, Any]) -> list[str]:
    """Submit peers that each need the other's startup before they can finish."""
    with connect() as client:
        return [
            client.submit(
                {
                    "name": f"rendezvous-{index}",
                    "spec": {
                        "type": "function",
                        "function_target": "tests.helpers.family_matrix:sibling_rendezvous",
                    },
                },
                payload={"index": index},
            ).tid
            for index in range(2)
        ]


def sibling_rendezvous(payload: dict[str, Any]) -> str:
    """An honest cross-branch dependency exceeds the W1 progress assumptions."""
    index = int(payload["index"])
    with connect() as client:
        with client.context.queue(
            f"test.sibling.ready.{index}", persistent=True
        ) as ready:
            ready.write("peer-ready")
        _wait_release(
            client.context,
            f"test.sibling.ready.{1 - index}",
            {"index": index},
            "rendezvous_waiting",
        )
    return "rendezvous-completed"


class PostlaunchCheckpointFaultManager(SnapshotFaultManager):
    """Pause publication after real Process.start, retaining its older checkpoint."""

    def _register_tid_state(self) -> bool:
        residents = getattr(self, "_scheduler_residents", {})
        for tid, evidence in residents.items():
            if evidence.get("scheduling_class") == "finite" and evidence.get(
                "host_processes"
            ):
                self._queue("test.family.events").write(
                    json.dumps(
                        {"event": "postlaunch_checkpoint_paused", "child_tid": tid}
                    )
                )
                threading.Event().wait()
        return super()._register_tid_state()
