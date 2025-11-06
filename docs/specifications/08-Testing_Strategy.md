# Testing Strategy

This document outlines the comprehensive testing approach for the Weft system, covering unit tests, integration tests, system tests, and performance validation.

## Testing Philosophy

- **Correctness First**: Verify all system invariants and guarantees
- **Concurrency Safety**: Test under realistic concurrent conditions
- **Failure Modes**: Extensively test error conditions and recovery
- **Performance Validation**: Ensure system meets specified targets
- **Cross-Platform**: Validate on Linux, macOS, and Windows

## 1. Unit Testing

### TaskSpec Tests (`tests/test_taskspec.py`)
```python
class TestTaskSpec:
    # Validation Tests
    def test_valid_minimal_spec()
    def test_invalid_tid_format()
    def test_missing_required_fields()
    def test_invalid_state_transitions()
    
    # Default Application Tests
    def test_apply_defaults_idempotent()
    def test_defaults_dont_override_user_values()
    def test_auto_expansion_on_init()
    
    # Convenience Method Tests
    def test_set_status_transitions()
    def test_update_metrics()
    def test_check_limits_exceeded()
    
    # Immutability Tests
    def test_spec_frozen_after_creation()
    def test_io_frozen_after_creation()
    def test_state_remains_mutable()
    def test_metadata_remains_mutable()
    
    # Limits Structure Tests
    def test_limits_section_validation()
    def test_memory_limit_validation()
    def test_cpu_limit_validation()
    def test_fd_limit_validation()
```

### Task Tests (`tests/test_tasks.py`)
```python
class TestTask:
    # Initialization Tests
    def test_init_with_different_db_types()
    def test_init_requires_taskspec()
    def test_init_sets_process_title()
    def test_init_without_setproctitle_graceful()
    
    # Execution Tests
    def test_run_once_function()
    def test_run_once_command()
    def test_run_forever_loop()
    def test_timeout_enforcement()
    
    # Queue Tests
    def test_control_queue_commands()
    def test_input_queue_processing()
    def test_output_queue_writing()
    def test_reservation_pattern()
    
    # Process Title Tests
    def test_process_title_format()
    def test_process_title_updates_on_state_change()
    def test_process_title_truncation()
    def test_tid_short_uniqueness()
    def test_tid_mapping_registration()
    
    # Worker Tests
    def test_worker_is_task()
    def test_worker_has_no_timeout()
    def test_worker_spawn_child_uses_message_tid()
    def test_worker_registers_capabilities()
```

### Process Title Tests (`tests/test_process_titles.py`)
```python
class TestProcessTitles:
    # TID Management
    def test_tid_short_extraction()
    def test_tid_short_collision_probability()
    def test_tid_mapping_queue_format()
    
    # Title Formatting
    def test_title_format_basic()
    def test_title_format_with_details()
    def test_title_truncation_long_name()
    def test_title_truncation_long_error()
    
    # State Integration
    def test_title_updates_on_mark_running()
    def test_title_updates_on_mark_failed()
    def test_title_updates_on_mark_completed()
    def test_title_updates_on_mark_timeout()
    def test_title_updates_on_mark_killed()
    
    # OS Integration
    def test_setproctitle_import_check()
    def test_process_title_visible_in_ps()
    def test_process_findable_by_short_tid()
    def test_process_killable_by_pattern()
    
    # Mapping and Lookup
    def test_tid_mapping_saved_to_queue()
    def test_tid_lookup_from_short()
    def test_pid_in_mapping_correct()
```

### Executor Tests (`tests/test_executor.py`)
```python
class TestExecutor:
    # Function Execution
    def test_execute_simple_function()
    def test_execute_with_args_kwargs()
    def test_function_timeout()
    def test_function_exception_handling()
    def test_function_process_title_inheritance()
    
    # Command Execution
    def test_execute_simple_command()
    def test_command_with_env_vars()
    def test_command_timeout()
    def test_command_working_directory()
    def test_command_process_title_inheritance()
    
    # Cross-Platform Tests
    def test_preexec_fn_unix_only()
    def test_windows_wrapper_script()
    def test_setproctitle_in_subprocess()
```

### Worker Tests (`tests/test_worker.py`)
```python
class TestWorker:
    # Worker as Task
    def test_worker_follows_task_lifecycle()
    def test_worker_appears_in_process_list()
    def test_worker_responds_to_control_messages()
    
    # Spawn Functionality
    def test_worker_spawns_child_task()
    def test_message_id_becomes_child_tid()
    def test_worker_validates_against_registry()
    def test_worker_rejects_unknown_tasks()
    
    # Worker Hierarchy
    def test_worker_spawns_another_worker()
    def test_primordial_worker_bootstrap()
    def test_worker_registration_in_global_queue()
    
    # TID Correlation
    def test_tid_correlation_through_lifecycle()
    def test_tid_appears_in_all_logs()
    def test_tid_sorting_by_timestamp()
```

### Resource Monitor Tests (`tests/test_monitor.py`)
```python
class TestResourceMonitor:
    # Basic Monitoring
    def test_start_stop_monitoring()
    def test_get_current_metrics()
    def test_psutil_integration()
    
    # Limits Checking
    def test_memory_limit_detection()
    def test_cpu_limit_sustained_violation()
    def test_fd_limit_enforcement()
    def test_connection_limit_enforcement()
    
    # Error Handling
    def test_process_gone_handling()
    def test_permission_denied_graceful()
    def test_psutil_not_available()
    
    # Metrics History
    def test_max_metrics_tracking()
    def test_history_size_limit()
    def test_metric_accuracy()
```

## 2. Integration Testing

### Queue Integration (`tests/integration/test_queue_integration.py`)
```python
class TestQueueIntegration:
    def test_task_to_task_pipeline()
    def test_multiple_workers_single_queue()
    def test_queue_persistence_across_restarts()
    def test_high_throughput_processing()
    def test_reservation_pattern_atomicity()
    def test_exactly_once_delivery()
    
    def test_large_output_handling()
    def test_output_spillover_to_disk()
    def test_output_reference_integrity()
    def test_output_cleanup_policies()
```

### Context Integration (`tests/integration/test_context_integration.py`)
```python
class TestContextIntegration:
    def test_context_isolation()
    def test_cross_context_communication_blocked()
    def test_context_cleanup()
    def test_database_auto_creation()
    def test_concurrent_contexts()
    
    def test_weft_context_field_usage()
    def test_context_discovery()
    def test_context_switching()
```

### Resource Monitoring (`tests/integration/test_monitoring.py`)
```python
class TestResourceMonitoring:
    def test_memory_limit_enforcement()
    def test_cpu_limit_enforcement()
    def test_metrics_accuracy()
    def test_monitor_cleanup_on_exit()
    def test_resource_violation_alerts()
    
    def test_monitoring_overhead()
    def test_concurrent_monitoring()
    def test_monitor_failure_recovery()
```

### SimpleBroker Integration (`tests/integration/test_simplebroker.py`)
```python
class TestSimpleBrokerIntegration:
    def test_queue_auto_creation()
    def test_atomic_move_operations()
    def test_message_persistence()
    def test_concurrent_access()
    def test_database_cleanup()
    
    def test_timestamp_id_uniqueness()
    def test_message_ordering()
    def test_peek_and_delete_pattern()
```

## 3. System Testing

### End-to-End Scenarios (`tests/system/test_scenarios.py`)
```python
class TestScenarios:
    def test_simple_unix_executable()
    def test_interactive_python()
    def test_map_reduce_pipeline()
    def test_parallel_task_execution()
    def test_task_failure_recovery()
    def test_system_shutdown_graceful()
    
    def test_worker_hierarchy_bootstrap()
    def test_primordial_worker_recovery()
    def test_emergency_task_management()
    
    def test_process_title_end_to_end()
    def test_tid_lookup_workflow()
    def test_os_integration_commands()
```

### Cross-Platform Tests (`tests/system/test_cross_platform.py`)
```python
class TestCrossPlatform:
    @pytest.mark.skipif(os.name == 'nt', reason="Unix-specific")
    def test_unix_process_titles()
    
    @pytest.mark.skipif(os.name != 'nt', reason="Windows-specific") 
    def test_windows_process_titles()
    
    def test_multiprocessing_spawn_consistency()
    def test_setproctitle_availability()
    def test_psutil_cross_platform()
```

### Load Testing (`tests/system/test_load.py`)
```python
class TestLoad:
    def test_200_concurrent_tasks()
    def test_queue_throughput_1000_msgs_sec()
    def test_state_log_performance()
    def test_memory_usage_under_load()
    def test_database_performance()
    
    def test_worker_scaling()
    def test_context_isolation_under_load()
    def test_large_output_handling_load()
```

## 4. Performance Testing

### Benchmarks (`tests/performance/test_benchmarks.py`)
All benchmarks have "pytest.mark.slow" and are not executed by default. 
They are designed to be informative at this point.

```python
class TestPerformance:
    @pytest.mark.slow
    def test_task_creation_throughput(self):
        """Target: 100 tasks/second"""
        start_time = time.time()
        tasks_created = 0
        
        for i in range(1000):
            taskspec = create_test_taskspec()
            task = Task(taskspec)
            tasks_created += 1
        
        elapsed = time.time() - start_time
        throughput = tasks_created / elapsed
        
        assert throughput >= 100, f"Task creation too slow: {throughput:.1f}/sec"
    
    @pytest.mark.slow      
    def test_queue_message_throughput(self):
        """Target: 1000 messages/second"""
        queue = Queue("test.throughput")
        messages = ["test message"] * 10000
        
        start_time = time.time()
        for msg in messages:
            queue.write(msg)
        elapsed = time.time() - start_time
        
        throughput = len(messages) / elapsed
        assert throughput >= 1000, f"Queue throughput too slow: {throughput:.1f}/sec"
    
    @pytest.mark.slow             
    def test_monitor_overhead(self):
        """Target: <2% CPU overhead"""
        # Create monitoring process
        monitor = ResourceMonitor(LimitsSection(), polling_interval=0.1)
        
        # Measure overhead
        baseline_cpu = get_system_cpu()
        monitor.start_monitoring(os.getpid())
        time.sleep(10)  # Monitor for 10 seconds
        
        monitoring_cpu = get_system_cpu()
        overhead = monitoring_cpu - baseline_cpu
        
        assert overhead < 2.0, f"Monitoring overhead too high: {overhead:.1f}%"
    
    @pytest.mark.slow             
    def test_large_task_graph_execution(self):
        """Target: Handle complex task dependencies"""
        # Create 50-task dependency graph
        tasks = create_dependency_graph(50)
        
        start_time = time.time()
        results = execute_task_graph(tasks)
        elapsed = time.time() - start_time
        
        assert len(results) == 50
        assert elapsed < 30, f"Task graph execution too slow: {elapsed:.1f}s"
```

### Memory Profiling (`tests/performance/test_memory.py`)
```python
class TestMemoryUsage:
    @pytest.mark.slow
    def test_task_memory_overhead(self):
        """Target: <10MB per task"""
        baseline = get_memory_usage()
        
        tasks = []
        for i in range(10):
            taskspec = create_test_taskspec()
            task = Task(taskspec)
            tasks.append(task)
        
        peak_memory = get_memory_usage()
        overhead_per_task = (peak_memory - baseline) / 10
        
        assert overhead_per_task < 10, f"Task memory overhead: {overhead_per_task:.1f}MB"
    
    @pytest.mark.slow
    def test_queue_memory_scaling(self):
        """Verify queue memory scales linearly"""
        queue = Queue("test.scaling")
        
        # Measure memory with different message counts
        memory_points = []
        for msg_count in [100, 1000, 10000]:
            baseline = get_memory_usage()
            for i in range(msg_count):
                queue.write(f"message {i}")
            memory_points.append(get_memory_usage() - baseline)
        
        # Should scale roughly linearly
        ratio_1000_100 = memory_points[1] / memory_points[0]
        ratio_10000_1000 = memory_points[2] / memory_points[1]
        
        assert 8 <= ratio_1000_100 <= 12  # ~10x messages, ~10x memory
        assert 8 <= ratio_10000_1000 <= 12
```

## 5. Property-Based Testing

### Using Hypothesis (`tests/property/test_properties.py`)
```python
from hypothesis import given, strategies as st

class TestProperties:
    @given(st.builds(TaskSpec))
    def test_state_transitions_always_forward(self, taskspec):
        """Property: State transitions are always forward-only."""
        old_status = taskspec.state.status
        
        # Try all possible transitions
        for new_status in ["created", "spawning", "running", "completed", "failed"]:
            try:
                taskspec.state.status = new_status
                # Should only succeed for valid forward transitions
                assert is_valid_transition(old_status, new_status)
            except ValueError:
                # Should fail for invalid transitions
                assert not is_valid_transition(old_status, new_status)
    
    @given(st.lists(st.text()))
    def test_queue_delivery_exactly_once(self, messages):
        """Property: Messages are delivered exactly once."""
        queue = Queue("test.property")
        
        # Write all messages
        written_ids = []
        for msg in messages:
            msg_id = queue.write(msg)
            written_ids.append(msg_id)
        
        # Read all messages
        read_ids = []
        while True:
            msg_data = queue.read()
            if not msg_data:
                break
            read_ids.append(msg_data['timestamp'])
        
        # Should have exactly the same IDs
        assert sorted(written_ids) == sorted(read_ids)
    
    @given(st.integers(min_value=1))
    def test_resource_limits_enforced(self, limit):
        """Property: Resource limits are always enforced."""
        limits = LimitsSection(memory_mb=limit)
        monitor = ResourceMonitor(limits)
        
        # Create process that exceeds limit
        process = create_memory_hungry_process(limit * 2)  # 2x the limit
        monitor.start_monitoring(process.pid)
        
        # Should detect violation
        time.sleep(2)  # Give time for monitoring
        compliant, error = monitor.check_limits()
        
        assert not compliant
        assert "Memory" in error
```

## 6. Testing Hooks

### Test Fixtures (`tests/fixtures/`)
```python
# taskspecs.py
from simplebroker import Queue

def create_minimal_taskspec() -> TaskSpec:
    """Create minimal valid TaskSpec for testing."""
    tid_queue = Queue("tests.generated.tids")
    return TaskSpec(
        tid=str(tid_queue.generate_timestamp()),
        name="test-task",
        version="1.0",
        spec=SpecSection(
            type="function",
            function_target="tests.fixtures.dummy:dummy_function",
            limits=LimitsSection()
        ),
        io=IOSection(
            inputs={},
            outputs={"outbox": "test.outbox"},
            control={"ctrl_in": "test.ctrl_in", "ctrl_out": "test.ctrl_out"}
        ),
        state=StateSection(status="created"),
        metadata={}
    )

def create_invalid_taskspec() -> dict:
    """Create invalid TaskSpec data for testing validation."""
    return {
        "tid": "invalid-tid",  # Wrong format
        "name": "",  # Empty name
        "spec": {
            "type": "invalid"  # Invalid type
        }
    }

def create_pipeline_taskspecs() -> list[TaskSpec]:
    """Create connected TaskSpecs for pipeline testing."""
    task1 = create_minimal_taskspec()
    task2 = create_minimal_taskspec()
    
    # Connect task1 output to task2 input
    task2.io.inputs["inbox"] = task1.io.outputs["outbox"]
    
    return [task1, task2]

# queues.py
@pytest.fixture
def test_context():
    """Create isolated test context."""
    temp_dir = tempfile.mkdtemp()
    context = WeftContext(temp_dir)
    yield context
    shutil.rmtree(temp_dir)

def create_test_queue(name: str, context: WeftContext) -> Queue:
    """Create test queue in isolated context."""
    return context.get_queue(name)

def populate_queue(queue: Queue, messages: list):
    """Add test messages to queue."""
    for msg in messages:
        queue.write(msg)

# mocks.py
def mock_executor() -> Mock:
    """Mock executor for testing without actual execution."""
    executor = Mock()
    executor.execute.return_value = ExecutionResult(
        success=True,
        return_code=0,
        stdout="test output",
        stderr="",
        execution_time=1.0
    )
    return executor

def mock_monitor() -> Mock:
    """Mock resource monitor for testing."""
    monitor = Mock()
    monitor.get_current_metrics.return_value = ResourceMetrics(
        memory_mb=50.0,
        cpu_percent=25.0,
        open_files=5,
        connections=2
    )
    monitor.check_limits.return_value = (True, None)
    return monitor

@pytest.fixture
def mock_broker_db():
    """Mock SimpleBroker database for testing."""
    with patch('simplebroker.Queue') as mock_queue:
        yield mock_queue
```

### Test Utilities (`tests/utils.py`)
```python
def wait_for_status(task: Task, status: str, timeout: float = 5.0):
    """Wait for task to reach specified status."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        if task.taskspec.state.status == status:
            return True
        time.sleep(0.1)
    raise TimeoutError(f"Task did not reach status {status} within {timeout}s")

def assert_queue_empty(queue: Queue):
    """Assert that queue has no pending messages."""
    messages = list(queue.peek_all())
    assert len(messages) == 0, f"Queue not empty: {len(messages)} messages"

def capture_queue_output(queue: Queue) -> list[str]:
    """Capture all messages from queue."""
    messages = []
    while True:
        msg = queue.read()
        if not msg:
            break
        messages.append(msg)
    return messages

def run_task_with_timeout(task: Task, timeout: float):
    """Run task with timeout protection."""
    import signal
    
    def timeout_handler(signum, frame):
        raise TimeoutError(f"Task execution exceeded {timeout}s")
    
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(int(timeout))
    
    try:
        task.run()
    finally:
        signal.alarm(0)

def get_memory_usage() -> float:
    """Get current process memory usage in MB."""
    import psutil
    process = psutil.Process()
    return process.memory_info().rss / (1024 * 1024)

def get_system_cpu() -> float:
    """Get current system CPU usage."""
    import psutil
    return psutil.cpu_percent(interval=1.0)

def create_memory_hungry_process(target_mb: int) -> subprocess.Popen:
    """Create process that consumes specified memory."""
    script = f"""
import time
data = b'x' * ({target_mb} * 1024 * 1024)
time.sleep(60)  # Keep memory allocated
"""
    return subprocess.Popen([sys.executable, "-c", script])

def is_valid_transition(old_status: str, new_status: str) -> bool:
    """Check if state transition is valid."""
    valid_transitions = {
        "created": {"spawning", "failed", "cancelled"},
        "spawning": {"running", "completed", "failed", "timeout", "cancelled", "killed"},
        "running": {"completed", "failed", "timeout", "cancelled", "killed"},
    }
    
    if old_status in valid_transitions:
        return new_status in valid_transitions[old_status]
    
    # Terminal states cannot transition
    return old_status == new_status
```

## Test Configuration

### pytest.ini
```ini
[tool:pytest]
minversion = 6.0
addopts = 
    -ra 
    --strict-markers 
    --strict-config
    --cov=weft
    --cov-report=term-missing
    --cov-report=html
    --cov-fail-under=90
testpaths = tests
markers =
    slow: marks tests as slow (deselect with '-m "not slow"')
    integration: marks tests as integration tests
    system: marks tests as system tests
    performance: marks tests as performance tests
    windows: marks tests as Windows-specific
    unix: marks tests as Unix-specific
```

### Test Automation
```bash
# Run all tests except slow ones (default)
pytest

# Run only unit tests
pytest tests/test_*.py

# Run integration tests
pytest tests/integration/

# Run system tests
pytest tests/system/

# Run performance tests (when needed)
pytest -m performance

# Run with coverage
pytest --cov=weft --cov-report=html

# Cross-platform testing
pytest -m "not windows"  # On Unix
pytest -m "not unix"     # On Windows
```

## Continuous Integration

### GitHub Actions (`.github/workflows/test.yml`)
```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        python-version: [3.8, 3.9, 3.10, 3.11]

    steps:
    - uses: actions/checkout@v3
    
    - name: Set up Python ${{ matrix.python-version }}
      uses: actions/setup-python@v4
      with:
        python-version: ${{ matrix.python-version }}
    
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -e .[dev,observability]
    
    - name: Run unit tests
      run: pytest tests/test_*.py -v
    
    - name: Run integration tests
      run: pytest tests/integration/ -v
    
    - name: Run system tests
      run: pytest tests/system/ -v
      
    - name: Upload coverage
      uses: codecov/codecov-action@v3
```

## Related Documents

- **[00-Overview_and_Architecture.md](00-Overview_and_Architecture.md)** - System overview and design principles
- **[01-TaskSpec.md](01-TaskSpec.md)** - Task configuration specification
- **[02-Core_Components.md](02-Core_Components.md)** - Detailed component architecture
- **[03-Worker_Architecture.md](03-Worker_Architecture.md)** - Recursive worker model
- **[07-System_Invariants.md](07-System_Invariants.md)** - System guarantees and constraints
- **[09-Implementation_Plan.md](09-Implementation_Plan.md)** - Development roadmap
