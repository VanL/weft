"""Tests for TaskSpec convenience methods."""

import time

import pytest

from tests.fixtures import taskspecs as fixtures


class TestStateManagement:
    """Test state management convenience methods."""

    def test_mark_started(self):
        """Test marking task as started."""
        taskspec = fixtures.create_minimal_taskspec()
        assert taskspec.state.status == "created"
        assert taskspec.state.started_at is None
        assert taskspec.state.pid is None

        taskspec.mark_started(pid=12345)

        assert taskspec.state.status == "spawning"
        assert taskspec.state.started_at is not None
        assert taskspec.state.pid == 12345

    def test_mark_running(self):
        """Test marking task as running."""
        taskspec = fixtures.create_minimal_taskspec()
        taskspec.mark_running(pid=12345)

        assert taskspec.state.status == "running"
        assert taskspec.state.started_at is not None
        assert taskspec.state.pid == 12345

    def test_mark_completed(self):
        """Test marking task as completed."""
        taskspec = fixtures.create_minimal_taskspec()
        taskspec.mark_running()
        taskspec.mark_completed(return_code=0)

        assert taskspec.state.status == "completed"
        assert taskspec.state.return_code == 0
        assert taskspec.state.completed_at is not None

    def test_mark_failed(self):
        """Test marking task as failed."""
        taskspec = fixtures.create_minimal_taskspec()
        taskspec.mark_running()
        taskspec.mark_failed(error="Something went wrong", return_code=1)

        assert taskspec.state.status == "failed"
        assert taskspec.state.error == "Something went wrong"
        assert taskspec.state.return_code == 1
        assert taskspec.state.completed_at is not None

    def test_mark_timeout(self):
        """Test marking task as timed out."""
        taskspec = fixtures.create_minimal_taskspec()
        taskspec.spec.timeout = 30.0
        taskspec.mark_running()
        taskspec.mark_timeout()

        assert taskspec.state.status == "timeout"
        assert "timed out after 30" in taskspec.state.error
        assert taskspec.state.completed_at is not None

    def test_mark_cancelled(self):
        """Test marking task as cancelled."""
        taskspec = fixtures.create_minimal_taskspec()
        taskspec.mark_cancelled(reason="User requested cancellation")

        assert taskspec.state.status == "cancelled"
        assert "Cancelled: User requested cancellation" in taskspec.state.error
        assert taskspec.state.completed_at is not None

    def test_mark_killed(self):
        """Test marking task as killed."""
        taskspec = fixtures.create_minimal_taskspec()
        taskspec.mark_running()
        taskspec.mark_killed(reason="Memory limit exceeded")

        assert taskspec.state.status == "killed"
        assert "Killed: Memory limit exceeded" in taskspec.state.error
        assert taskspec.state.completed_at is not None

    def test_set_status_with_valid_transition(self):
        """Test set_status with valid state transitions."""
        taskspec = fixtures.create_minimal_taskspec()

        # created -> spawning
        taskspec.set_status("spawning")
        assert taskspec.state.status == "spawning"

        # spawning -> running
        taskspec.set_status("running")
        assert taskspec.state.status == "running"

        # running -> completed
        taskspec.set_status("completed")
        assert taskspec.state.status == "completed"

    def test_set_status_invalid_transition(self):
        """Test set_status with invalid state transition."""
        taskspec = fixtures.create_minimal_taskspec()
        taskspec.set_status("spawning")
        taskspec.set_status("running")

        # Try to go backwards from running to created (invalid)
        with pytest.raises(ValueError) as exc_info:
            taskspec.set_status("created")
        assert "Invalid transition" in str(exc_info.value)

    def test_set_status_from_terminal_state(self):
        """Test that terminal states cannot transition."""
        taskspec = fixtures.create_minimal_taskspec()
        taskspec.mark_completed()

        # Try to transition from completed (terminal) to running
        with pytest.raises(ValueError) as exc_info:
            taskspec.set_status("running")
        assert "Cannot transition from terminal state" in str(exc_info.value)


class TestMetricsUpdates:
    """Test metrics update convenience methods."""

    def test_update_metrics(self):
        """Test updating resource metrics."""
        taskspec = fixtures.create_minimal_taskspec()

        # Initial state - all metrics should be None
        assert taskspec.state.memory is None
        assert taskspec.state.cpu is None
        assert taskspec.state.fds is None

        # Update metrics
        taskspec.update_metrics(
            time=10.5, memory=256.7, cpu=45, fds=15, net_connections=3
        )

        assert taskspec.state.time == 10.5
        assert taskspec.state.memory == 256.7
        assert taskspec.state.cpu == 45
        assert taskspec.state.fds == 15
        assert taskspec.state.net_connections == 3

        # Check maximums were tracked
        assert taskspec.state.max_memory == 256.7
        assert taskspec.state.max_cpu == 45
        assert taskspec.state.max_fds == 15
        assert taskspec.state.max_net_connections == 3

    def test_update_metrics_tracks_maximums(self):
        """Test that update_metrics tracks maximum values."""
        taskspec = fixtures.create_minimal_taskspec()

        # First update
        taskspec.update_metrics(memory=100.0, cpu=20)
        assert taskspec.state.max_memory == 100.0
        assert taskspec.state.max_cpu == 20

        # Second update with higher values
        taskspec.update_metrics(memory=200.0, cpu=50)
        assert taskspec.state.max_memory == 200.0
        assert taskspec.state.max_cpu == 50

        # Third update with lower values - maximums should not change
        taskspec.update_metrics(memory=150.0, cpu=30)
        assert taskspec.state.memory == 150.0  # Current value updated
        assert taskspec.state.cpu == 30  # Current value updated
        assert taskspec.state.max_memory == 200.0  # Maximum unchanged
        assert taskspec.state.max_cpu == 50  # Maximum unchanged

    def test_check_limits_exceeded(self):
        """Test checking if resource limits are exceeded."""
        taskspec = fixtures.create_minimal_taskspec()
        taskspec.spec.memory_limit = 512
        taskspec.spec.cpu_limit = 80
        taskspec.spec.max_fds = 100
        taskspec.spec.max_connections = 10

        # Initially no limits exceeded
        exceeded, reason = taskspec.check_limits_exceeded()
        assert exceeded is False
        assert reason is None

        # Update metrics within limits
        taskspec.update_metrics(memory=256.0, cpu=50, fds=50, net_connections=5)
        exceeded, reason = taskspec.check_limits_exceeded()
        assert exceeded is False

        # Exceed memory limit
        taskspec.update_metrics(memory=600.0)
        exceeded, reason = taskspec.check_limits_exceeded()
        assert exceeded is True
        assert "Memory limit exceeded" in reason

        # Reset memory, exceed CPU limit
        taskspec.update_metrics(memory=256.0, cpu=90)
        exceeded, reason = taskspec.check_limits_exceeded()
        assert exceeded is True
        assert "CPU limit exceeded" in reason

        # Reset CPU, exceed FD limit
        taskspec.update_metrics(cpu=50, fds=150)
        exceeded, reason = taskspec.check_limits_exceeded()
        assert exceeded is True
        assert "File descriptor limit exceeded" in reason

        # Reset FDs, exceed connections limit
        taskspec.update_metrics(fds=50, net_connections=15)
        exceeded, reason = taskspec.check_limits_exceeded()
        assert exceeded is True
        assert "Network connection limit exceeded" in reason


class TestMetadataManagement:
    """Test metadata management convenience methods."""

    def test_update_metadata(self):
        """Test updating metadata dictionary."""
        taskspec = fixtures.create_minimal_taskspec()
        assert taskspec.metadata == {}

        taskspec.update_metadata({"key1": "value1", "key2": 42})
        assert taskspec.metadata == {"key1": "value1", "key2": 42}

        # Update with more values
        taskspec.update_metadata({"key2": 100, "key3": "new"})
        assert taskspec.metadata == {"key1": "value1", "key2": 100, "key3": "new"}

    def test_set_metadata(self):
        """Test setting individual metadata values."""
        taskspec = fixtures.create_minimal_taskspec()

        taskspec.set_metadata("owner", "user123")
        assert taskspec.metadata["owner"] == "user123"

        taskspec.set_metadata("priority", 5)
        assert taskspec.metadata["priority"] == 5

    def test_get_metadata(self):
        """Test getting metadata values."""
        taskspec = fixtures.create_minimal_taskspec()
        taskspec.metadata = {"key1": "value1", "key2": 42}

        assert taskspec.get_metadata("key1") == "value1"
        assert taskspec.get_metadata("key2") == 42
        assert taskspec.get_metadata("missing") is None
        assert taskspec.get_metadata("missing", "default") == "default"


class TestQueueManagement:
    """Test queue management convenience methods."""

    def test_add_input_queue(self):
        """Test adding input queues."""
        taskspec = fixtures.create_minimal_taskspec()

        taskspec.add_input_queue("data", "T1234.data")
        assert taskspec.io.inputs["data"] == "T1234.data"

        taskspec.add_input_queue("config", "T1234.config")
        assert taskspec.io.inputs["config"] == "T1234.config"

    def test_add_output_queue(self):
        """Test adding output queues."""
        taskspec = fixtures.create_minimal_taskspec()

        taskspec.add_output_queue("logs", "T1234.logs")
        assert taskspec.io.outputs["logs"] == "T1234.logs"

        # Should allow adding to existing outbox
        existing_outbox = taskspec.io.outputs["outbox"]
        taskspec.add_output_queue("metrics", "T1234.metrics")
        assert taskspec.io.outputs["outbox"] == existing_outbox
        assert taskspec.io.outputs["metrics"] == "T1234.metrics"

    def test_cannot_override_outbox(self):
        """Test that outbox queue cannot be overridden with different path."""
        taskspec = fixtures.create_minimal_taskspec()
        original_outbox = taskspec.io.outputs["outbox"]

        # Should raise error when trying to change outbox
        with pytest.raises(ValueError) as exc_info:
            taskspec.add_output_queue("outbox", "different.path")
        assert "Cannot override required 'outbox' queue" in str(exc_info.value)

        # Outbox should remain unchanged
        assert taskspec.io.outputs["outbox"] == original_outbox

    def test_get_queue_path(self):
        """Test getting queue paths."""
        taskspec = fixtures.create_minimal_taskspec()
        taskspec.io.inputs["data"] = "T1234.data"

        assert taskspec.get_queue_path("inputs", "data") == "T1234.data"
        assert taskspec.get_queue_path("outputs", "outbox") is not None
        assert taskspec.get_queue_path("control", "ctrl_in") is not None
        assert taskspec.get_queue_path("control", "ctrl_out") is not None
        assert taskspec.get_queue_path("inputs", "missing") is None


class TestReportingMethods:
    """Test reporting convenience methods."""

    def test_get_runtime_seconds(self):
        """Test calculating runtime in seconds."""
        taskspec = fixtures.create_minimal_taskspec()

        # No runtime if not started
        assert taskspec.get_runtime_seconds() is None

        # Set started time
        start_time = time.time_ns()
        taskspec.state.started_at = start_time

        # Should calculate runtime to current time
        runtime = taskspec.get_runtime_seconds()
        assert runtime is not None
        assert runtime >= 0

        # Set completed time
        taskspec.state.completed_at = start_time + 5_000_000_000  # 5 seconds later
        runtime = taskspec.get_runtime_seconds()
        assert runtime == 5.0

    def test_should_report_transition_mode(self):
        """Test should_report in transition mode."""
        taskspec = fixtures.create_minimal_taskspec()
        taskspec.spec.reporting_interval = "transition"

        # Should report on status change
        assert taskspec.should_report(last_status="created") is False  # Same status
        assert taskspec.should_report(last_status=None) is True  # First report

        taskspec.mark_running()
        assert taskspec.should_report(last_status="created") is True  # Status changed
        assert taskspec.should_report(last_status="running") is False  # Same status

    def test_should_report_poll_mode(self):
        """Test should_report in poll mode."""
        taskspec = fixtures.create_minimal_taskspec()
        taskspec.spec.reporting_interval = "poll"

        # Should always report in poll mode
        assert taskspec.should_report(last_status="created") is True
        assert taskspec.should_report(last_status="running") is True
        assert taskspec.should_report(last_status=None) is True

    def test_to_log_dict(self):
        """Test converting TaskSpec to log dictionary."""
        taskspec = fixtures.create_minimal_taskspec()
        taskspec.mark_running(pid=12345)
        taskspec.update_metrics(memory=256.7, cpu=45)
        taskspec.set_metadata("owner", "user123")

        log_dict = taskspec.to_log_dict()

        assert log_dict["tid"] == taskspec.tid
        assert log_dict["name"] == taskspec.name
        assert log_dict["status"] == "running"
        assert log_dict["started_at"] is not None
        assert log_dict["completed_at"] is None
        assert log_dict["runtime_seconds"] is not None
        assert log_dict["memory_mb"] == 256.7
        assert log_dict["cpu_percent"] == 45
        assert log_dict["error"] is None
        assert log_dict["metadata"]["owner"] == "user123"
