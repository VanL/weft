"""Tests for the Task class."""

import tempfile
import threading
import time
from pathlib import Path

from simplebroker.db import BrokerDB
from simplebroker.sbqueue import Queue
from tests.fixtures import taskspecs as fixtures
from weft.core.tasks import Task
from weft.core.taskspec import IOSection, SpecSection, StateSection, TaskSpec


class TestTask:
    """Test Task class functionality."""

    def test_task_initialization_with_path(self):
        """Test Task can be initialized with a database path."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            taskspec = fixtures.create_minimal_taskspec()
            task = Task(tmp.name, taskspec)
            assert task.tid == taskspec.tid
            assert task.tid.isdigit()
            assert len(task.tid) == 19  # time_ns() produces 19-digit numbers

    def test_task_initialization_with_pathlib(self):
        """Test Task initialization with Path object."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            taskspec = fixtures.create_minimal_taskspec()
            task = Task(Path(tmp.name), taskspec)
            assert task.tid == taskspec.tid
            assert isinstance(task.db_path, str)

    def test_task_initialization_with_brokerdb(self):
        """Test Task initialization with BrokerDB instance."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = BrokerDB(tmp.name)
            taskspec = fixtures.create_minimal_taskspec()
            task = Task(db, taskspec)
            assert task.tid == taskspec.tid
            assert task.db_path == str(db.db_path)

    def test_task_with_stop_event(self):
        """Test Task with custom stop event."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            stop_event = threading.Event()
            taskspec = fixtures.create_minimal_taskspec()
            task = Task(tmp.name, taskspec, stop_event=stop_event)
            assert task._stop_event is stop_event

    def test_task_inherits_from_basewatcher(self):
        """Test that Task properly inherits from BaseWatcher."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            taskspec = fixtures.create_minimal_taskspec()
            task = Task(tmp.name, taskspec)
            from simplebroker.watcher import BaseWatcher

            assert isinstance(task, BaseWatcher)
            assert hasattr(task, "_drain_queue")

    def test_required_queues(self):
        """Test that TaskSpec must have required queues."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            taskspec = fixtures.create_minimal_taskspec()
            task = Task(tmp.name, taskspec)

            # Check that required queues exist (auto-expanded)
            assert "ctrl_in" in task.taskspec.io.control
            assert "ctrl_out" in task.taskspec.io.control
            assert "outbox" in task.taskspec.io.outputs

    def test_custom_taskspec_queues(self):
        """Test that custom queues from taskspec are used."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            taskspec = TaskSpec(
                tid="1234567890123456789",
                version="1.0",
                name="test-task",
                spec=SpecSection(type="command", process_target=["echo"]),
                io=IOSection(
                    inputs={
                        "data": "custom.data.queue",
                        "config": "custom.config.queue",
                    },
                    outputs={"outbox": "custom.outbox"},
                    control={
                        "ctrl_in": "custom.control.in",
                        "ctrl_out": "custom.control.out",
                    },
                ),
                state=StateSection(),
            )

            task = Task(tmp.name, taskspec=taskspec)

            # Check custom queues are preserved
            assert task.taskspec.io.inputs["data"] == "custom.data.queue"
            assert task.taskspec.io.inputs["config"] == "custom.config.queue"
            assert task.taskspec.io.outputs["outbox"] == "custom.outbox"
            assert task.taskspec.io.control["ctrl_in"] == "custom.control.in"
            assert task.taskspec.io.control["ctrl_out"] == "custom.control.out"

    def test_control_queue_stop_command(self):
        """Test that STOP command on control queue sets should_stop."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            taskspec = fixtures.create_minimal_taskspec()
            task = Task(tmp.name, taskspec)

            # Write STOP to control queue
            ctrl_queue = Queue(task.taskspec.io.control["ctrl_in"], db_path=tmp.name)
            ctrl_queue.write("STOP")

            # Process control queue
            task._check_control_queue()

            # Verify task should stop
            assert task.should_stop is True

    def test_control_queue_unknown_command(self):
        """Test that unknown control commands are ignored."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            taskspec = fixtures.create_minimal_taskspec()
            task = Task(tmp.name, taskspec)

            # Write unknown command to control queue
            ctrl_queue = Queue(task.taskspec.io.control["ctrl_in"], db_path=tmp.name)
            ctrl_queue.write("UNKNOWN")

            # Process control queue
            task._check_control_queue()

            # Verify task continues running
            assert task.should_stop is False

    def test_drain_queue_checks_control_first(self):
        """Test that _drain_queue checks control queue first."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            taskspec = fixtures.create_minimal_taskspec()
            task = Task(tmp.name, taskspec)

            # Write STOP to control queue
            ctrl_queue = Queue(task.taskspec.io.control["ctrl_in"], db_path=tmp.name)
            ctrl_queue.write("STOP")

            # Call drain_queue
            task._drain_queue()

            # Should have detected STOP and set should_stop
            assert task.should_stop is True

    def test_input_queue_processing(self):
        """Test that messages from input queues are processed."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            # Create a task with an explicit inbox queue since it's optional
            taskspec = TaskSpec(
                tid="1234567890123456789",
                version="1.0",
                name="test-task",
                spec=SpecSection(type="function", function_target="test:func"),
                io=IOSection(
                    inputs={
                        "inbox": "test.inbox"
                    },  # Explicitly add inbox for this test
                    outputs={"outbox": "test.outbox"},
                    control={
                        "ctrl_in": "test.ctrl_in",
                        "ctrl_out": "test.ctrl_out",
                    },
                ),
                state=StateSection(),
            )
            task = Task(tmp.name, taskspec=taskspec)

            # Write message to inbox
            inbox_queue = Queue(task.taskspec.io.inputs["inbox"], db_path=tmp.name)
            test_message = "Test input message"
            inbox_queue.write(test_message)

            # Process input queue
            task._process_input_queue(task.taskspec.io.inputs["inbox"])

            # The message should be read (queue should be empty now)
            assert inbox_queue.read() is None

    def test_multiple_input_queues(self):
        """Test processing messages from multiple input queues."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            taskspec = TaskSpec(
                tid="1234567890123456789",
                version="1.0",
                name="multi-queue-task",
                spec=SpecSection(type="function", function_target="test:func"),
                io=IOSection(
                    inputs={
                        "queue1": "test.queue1",
                        "queue2": "test.queue2",
                        "queue3": "test.queue3",
                    },
                    outputs={"outbox": "test.outbox"},  # REQUIRED per spec
                    control={
                        "ctrl_in": "test.ctrl_in",  # REQUIRED per spec
                        "ctrl_out": "test.ctrl_out",  # REQUIRED per spec
                    },
                ),
                state=StateSection(),
            )

            task = Task(tmp.name, taskspec=taskspec)

            # Write messages to all input queues
            for queue_name, queue_path in task.taskspec.io.inputs.items():
                queue = Queue(queue_path, db_path=tmp.name)
                queue.write(f"Message for {queue_name}")

            # Process all queues via drain
            task._drain_queue()

            # All messages should be processed
            for queue_path in task.taskspec.io.inputs.values():
                queue = Queue(queue_path, db_path=tmp.name)
                assert queue.read() is None

    def test_run_forever_updates_status(self):
        """Test that run_forever updates task status correctly."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            taskspec = fixtures.create_minimal_taskspec()
            task = Task(tmp.name, taskspec=taskspec)

            # Set up to stop immediately
            task.should_stop = True

            # Run the task
            task.run_forever()

            # Check status was updated
            assert task.taskspec.state.status == "completed"
            assert task.taskspec.state.started_at is not None
            assert task.taskspec.state.completed_at is not None
            assert task.taskspec.state.completed_at > task.taskspec.state.started_at

    def test_run_forever_with_stop_event(self):
        """Test that run_forever stops when stop_event is set."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            stop_event = threading.Event()
            taskspec = fixtures.create_minimal_taskspec()
            task = Task(tmp.name, taskspec, stop_event=stop_event)

            # Run task in thread
            task_thread = threading.Thread(target=task.run_forever)
            task_thread.start()

            # Give it a moment to start
            time.sleep(0.1)

            # Set stop event
            stop_event.set()

            # Wait for task to stop
            task_thread.join(timeout=2.0)

            # Verify task stopped
            assert not task_thread.is_alive()
            assert task.taskspec.state.completed_at is not None

    def test_to_json_serialization(self):
        """Test TaskSpec serialization to JSON."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            taskspec = fixtures.create_valid_function_taskspec()
            task = Task(tmp.name, taskspec)

            json_str = task.to_json()
            assert isinstance(json_str, str)
            assert task.tid in json_str
            assert task.taskspec.name in json_str

    def test_from_json_deserialization(self):
        """Test Task creation from JSON TaskSpec."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            # Create original task
            original_spec = fixtures.create_valid_function_taskspec()
            original_task = Task(tmp.name, original_spec)

            # Serialize to JSON
            json_str = original_task.to_json()

            # Create new task from JSON
            new_task = Task.from_json(json_str, db=tmp.name)

            assert new_task.tid == original_task.tid
            assert new_task.taskspec.name == original_task.taskspec.name
            assert (
                new_task.taskspec.spec.function_target
                == original_task.taskspec.spec.function_target
            )

    def test_task_database_path_handling(self):
        """Test that Task correctly handles different database path types."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            taskspec = fixtures.create_minimal_taskspec()

            # Test with string path
            task = Task(tmp.name, taskspec)
            assert task.db_path == tmp.name

            # Test with Path object
            task = Task(Path(tmp.name), taskspec)
            assert task.db_path == str(Path(tmp.name))

            # Test with BrokerDB instance
            db = BrokerDB(tmp.name)
            task = Task(db, taskspec)
            assert task.db_path == str(db.db_path)
