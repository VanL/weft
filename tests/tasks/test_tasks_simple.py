"""Simplified tests for the Task class that work with current implementation."""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

import pytest

from simplebroker.db import BrokerDB
from tests.fixtures import taskspecs as fixtures
from weft.core.tasks import Consumer
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec

pytestmark = [pytest.mark.sqlite_only]


class TestTaskSimple:
    """Test Task class basic functionality."""

    @staticmethod
    def _db_path() -> tuple[tempfile.TemporaryDirectory[str], Path]:
        tempdir = tempfile.TemporaryDirectory()
        return tempdir, Path(tempdir.name) / "task.db"

    def test_task_initialization_with_path(self):
        """Test Task can be initialized with a database path."""
        tempdir, db_path = self._db_path()
        try:
            taskspec = fixtures.create_minimal_taskspec()
            task = Consumer(str(db_path), taskspec)
            assert task.tid == taskspec.tid
            assert task.tid.isdigit()
            assert len(task.tid) == 19
            task.cleanup()
        finally:
            tempdir.cleanup()

    def test_task_initialization_with_pathlib(self):
        """Test Task initialization with Path object."""
        tempdir, db_path = self._db_path()
        try:
            taskspec = fixtures.create_minimal_taskspec()
            task = Consumer(db_path, taskspec)
            assert task.tid == taskspec.tid
            assert hasattr(task, "taskspec")
            task.cleanup()
        finally:
            tempdir.cleanup()

    def test_task_initialization_with_brokerdb(self):
        """Test Task initialization with BrokerDB instance."""
        tempdir, db_path = self._db_path()
        db = BrokerDB(str(db_path))
        try:
            taskspec = fixtures.create_minimal_taskspec()
            task = Consumer(db, taskspec)
            assert task.tid == taskspec.tid
            assert hasattr(task, "taskspec")
            task.cleanup()
        finally:
            db.close()
            tempdir.cleanup()

    def test_task_with_stop_event(self):
        """Test Task with custom stop event."""
        tempdir, db_path = self._db_path()
        try:
            stop_event = threading.Event()
            taskspec = fixtures.create_minimal_taskspec()
            task = Consumer(str(db_path), taskspec, stop_event=stop_event)
            assert task.tid == taskspec.tid
            assert task._stop_event is stop_event
            task.cleanup()
        finally:
            tempdir.cleanup()

    def test_task_provides_default_stop_event(self):
        """Task should always expose a functioning stop event."""
        tempdir, db_path = self._db_path()
        try:
            taskspec = fixtures.create_minimal_taskspec()
            task = Consumer(str(db_path), taskspec)

            assert task._stop_event is not None

            # stop() should succeed even if the watcher thread was never started
            task.stop()
        finally:
            tempdir.cleanup()

    def test_task_inherits_from_multiqueue_watcher(self):
        """Test that Task properly inherits from MultiQueueWatcher."""
        tempdir, db_path = self._db_path()
        try:
            taskspec = fixtures.create_minimal_taskspec()
            task = Consumer(str(db_path), taskspec)
            from weft.core.tasks.multiqueue_watcher import MultiQueueWatcher

            assert isinstance(task, MultiQueueWatcher)
            assert hasattr(task, "taskspec")
            task.cleanup()
        finally:
            tempdir.cleanup()

    def test_required_queues(self):
        """Task resolves all five default reactor queue roles."""
        tempdir, db_path = self._db_path()
        try:
            taskspec = fixtures.create_minimal_taskspec()
            task = Consumer(str(db_path), taskspec)

            prefix = f"T{taskspec.tid}"
            expected = {
                "inbox": f"{prefix}.inbox",
                "reserved": f"{prefix}.reserved",
                "outbox": f"{prefix}.outbox",
                "ctrl_in": f"{prefix}.ctrl_in",
                "ctrl_out": f"{prefix}.ctrl_out",
            }
            assert task._reactor_queue_roles() == expected
            assert task._ctrl_out_queue.name == expected["ctrl_out"]
            task.cleanup()
        finally:
            tempdir.cleanup()

    def test_custom_taskspec_queues(self):
        """Canonical custom queues drive the reactor; extra inputs do not."""
        tempdir, db_path = self._db_path()
        try:
            taskspec = TaskSpec(
                tid=fixtures.VALID_TEST_TID,
                version="1.0",
                name="test-task",
                spec=SpecSection(type="command", process_target="echo"),
                io=IOSection(
                    inputs={
                        "inbox": "custom.inbox",
                        "data": "custom.data.queue",
                    },
                    outputs={"outbox": "custom.outbox"},
                    control={
                        "ctrl_in": "custom.control.in",
                        "ctrl_out": "custom.control.out",
                    },
                ),
                state=StateSection(),
            )

            task = Consumer(str(db_path), taskspec=taskspec)

            roles = task._reactor_queue_roles()
            assert roles == {
                "inbox": "custom.inbox",
                "reserved": f"T{taskspec.tid}.reserved",
                "outbox": "custom.outbox",
                "ctrl_in": "custom.control.in",
                "ctrl_out": "custom.control.out",
            }
            assert task._ctrl_out_queue.name == "custom.control.out"
            assert "data" not in roles
            assert "custom.data.queue" not in roles.values()
            task.cleanup()
        finally:
            tempdir.cleanup()

    def test_task_has_basic_attributes(self):
        """Test that task has all basic attributes."""
        tempdir, db_path = self._db_path()
        try:
            taskspec = fixtures.create_minimal_taskspec()
            task = Consumer(str(db_path), taskspec)

            assert hasattr(task, "tid")
            assert hasattr(task, "taskspec")
            assert task.tid == taskspec.tid
            assert task.taskspec is taskspec
            assert task._queue_names["reserved"].endswith(".reserved")
            task.cleanup()
        finally:
            tempdir.cleanup()
