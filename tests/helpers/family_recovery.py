"""External snapshot-publication fault for real launch-custody recovery tests."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from weft.core.manager import Manager
from weft.core.tasks.base import BaseTask
from weft.core.tasks.consumer import Consumer
from weft.core.taskspec import TaskSpec


class SnapshotFaultConsumer(Consumer):
    """Suppress publication until the test permits the next ordinary edge."""

    def _register_tid_state(self) -> bool:
        root = self.taskspec.spec.weft_context
        if (
            root is None
            or not (Path(root) / ".weft" / "allow-family-snapshot").exists()
        ):
            return False
        return super()._register_tid_state()


class SnapshotFaultManager(Manager):
    """Use the real Manager; fault only fixture Consumer publications."""

    @staticmethod
    def _resolve_child_task_class(child_spec: TaskSpec) -> type[BaseTask]:
        if child_spec.name == "family-recovery-parent":
            return SnapshotFaultConsumer
        return Manager._resolve_child_task_class(child_spec)


class PrecheckpointFaultManager(Manager):
    """Hold one real publication before the mandatory pre-launch fence commits."""

    def _register_tid_state(self) -> bool:
        residents = getattr(self, "_scheduler_residents", {})
        for tid, evidence in residents.items():
            if evidence.get("scheduling_class") == "finite" and not evidence.get(
                "host_processes"
            ):
                root = self.taskspec.spec.weft_context
                if root is None:
                    raise RuntimeError("fixture Manager has no context")
                (Path(root) / ".weft" / "reserved-before-checkpoint").write_text(tid)
                # An unavailable publication boundary must not permit Process.start.
                # The test crashes this process while the real checkpoint call waits.
                self._queue("test.family.events").write(
                    json.dumps(
                        {"event": "reserved_before_checkpoint", "child_tid": tid}
                    )
                )
                threading.Event().wait()
        return super()._register_tid_state()
