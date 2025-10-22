"""TaskSpec fixtures for testing."""

from typing import Any

from weft.core.taskspec import (
    IOSection,
    LimitsSection,
    SpecSection,
    StateSection,
    TaskSpec,
)

# Use a current valid timestamp for tests
VALID_TEST_TID = "1755033993077017000"


def create_minimal_taskspec(tid: str = VALID_TEST_TID) -> TaskSpec:
    """Create a minimal valid TaskSpec with all required fields."""
    return TaskSpec(
        tid=tid,
        name=f"test-{tid}",
        spec=SpecSection(
            type="function",
            function_target="test:func",
        ),
        io=IOSection(
            outputs={"outbox": f"T{tid}.outbox"},
            control={
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        ),
        state=StateSection(),
    )


def create_valid_function_taskspec(
    tid: str = VALID_TEST_TID,
    name: str = "test-function",
    function_target: str = "module:func",
) -> TaskSpec:
    """Create a valid function TaskSpec with common fields."""
    return TaskSpec(
        tid=tid,
        name=name,
        spec=SpecSection(
            type="function",
            function_target=function_target,
            args=[1, 2, 3],
            keyword_args={"key": "value"},
            timeout=30.0,
            limits=LimitsSection(memory_mb=256),
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        ),
        state=StateSection(),
        metadata={"owner": "test", "tags": ["test", "example"]},
    )


def create_valid_command_taskspec(
    tid: str = VALID_TEST_TID,
    name: str = "test-command",
    process_target: list[str] | None = None,
) -> TaskSpec:
    """Create a valid command TaskSpec with common fields."""
    if process_target is None:
        process_target = ["echo", "hello"]

    return TaskSpec(
        tid=tid,
        name=name,
        spec=SpecSection(
            type="command",
            process_target=process_target,
            timeout=30.0,
            limits=LimitsSection(memory_mb=256),
            env={"TEST_VAR": "test_value"},
            working_dir="/tmp",
        ),
        io=IOSection(
            inputs={"stdin": f"T{tid}.stdin"},
            outputs={
                "outbox": f"T{tid}.outbox",
                "stdout": f"T{tid}.stdout",
                "stderr": f"T{tid}.stderr",
            },
            control={
                "ctrl_in": f"T{tid}.ctrl_in",
                "ctrl_out": f"T{tid}.ctrl_out",
            },
        ),
        state=StateSection(),
        metadata={"owner": "test", "type": "command"},
    )


# Dictionary representations for JSON testing
MINIMAL_VALID_TASKSPEC_DICT: dict[str, Any] = {
    "tid": VALID_TEST_TID,
    "name": "test-minimal",
    "spec": {
        "type": "function",
        "function_target": "test:func",
        "reserved_policy_on_stop": "keep",
        "reserved_policy_on_error": "keep",
    },
    "io": {
        "inputs": {},  # Can be empty per spec
        "outputs": {"outbox": f"T{VALID_TEST_TID}.outbox"},
        "control": {
            "ctrl_in": f"T{VALID_TEST_TID}.ctrl_in",
            "ctrl_out": f"T{VALID_TEST_TID}.ctrl_out",
        },
    },
    "state": {
        "status": "created",
        "return_code": None,
        "started_at": None,
        "completed_at": None,
    },
    "metadata": {},  # REQUIRED per spec but can be empty
}

VALID_FUNCTION_TASKSPEC_DICT: dict[str, Any] = {
    "tid": VALID_TEST_TID,
    "name": "test-function",
    "version": "1.0",
    "description": "A test function task",
    "spec": {
        "type": "function",
        "function_target": "module:func",
        "args": [1, 2, 3],
        "keyword_args": {"key": "value"},
        "timeout": 30.0,
        "limits": {
            "memory_mb": 256,
            "cpu_percent": 50,
        },
        "stream_output": False,
        "cleanup_on_exit": True,
        "polling_interval": 1.0,
        "reporting_interval": "transition",
        "reserved_policy_on_stop": "keep",
        "reserved_policy_on_error": "keep",
    },
    "io": {
        "inputs": {"inbox": f"T{VALID_TEST_TID}.inbox"},
        "outputs": {"outbox": f"T{VALID_TEST_TID}.outbox"},
        "control": {
            "ctrl_in": f"T{VALID_TEST_TID}.ctrl_in",
            "ctrl_out": f"T{VALID_TEST_TID}.ctrl_out",
        },
    },
    "state": {
        "status": "created",
        "pid": None,
        "return_code": None,
        "started_at": None,
        "completed_at": None,
        "error": None,
        "time": None,
        "memory": None,
        "cpu": None,
    },
    "metadata": {
        "owner": "test",
        "tags": ["test", "example"],
    },
}

VALID_COMMAND_TASKSPEC_DICT: dict[str, Any] = {
    "tid": VALID_TEST_TID,
    "name": "test-command",
    "version": "1.0",
    "spec": {
        "type": "command",
        "process_target": ["echo", "hello"],
        "timeout": 30.0,
        "limits": {
            "memory_mb": 256,
        },
        "env": {"TEST_VAR": "test_value"},
        "working_dir": "/tmp",
        "stream_output": True,
        "reserved_policy_on_stop": "keep",
        "reserved_policy_on_error": "keep",
    },
    "io": {
        "inputs": {"stdin": f"T{VALID_TEST_TID}.stdin"},
        "outputs": {
            "outbox": f"T{VALID_TEST_TID}.outbox",
            "stdout": f"T{VALID_TEST_TID}.stdout",
            "stderr": f"T{VALID_TEST_TID}.stderr",
        },
        "control": {
            "ctrl_in": f"T{VALID_TEST_TID}.ctrl_in",
            "ctrl_out": f"T{VALID_TEST_TID}.ctrl_out",
        },
    },
    "state": {
        "status": "created",
        "return_code": None,
        "started_at": None,
        "completed_at": None,
    },
    "metadata": {
        "owner": "test",
        "type": "command",
    },
}


# Invalid TaskSpec dictionaries for testing validation
INVALID_MISSING_TID: dict[str, Any] = {
    # Missing tid
    "name": "test",
    "spec": {"type": "function", "function_target": "test:func"},
}

INVALID_SHORT_TID: dict[str, Any] = {
    "tid": "123",  # Too short
    "name": "test",
    "spec": {"type": "function", "function_target": "test:func"},
    "io": {
        "inputs": {},
        "outputs": {"outbox": "test.out"},
        "control": {"ctrl_in": "test.in", "ctrl_out": "test.out"},
    },
    "state": {
        "status": "created",
        "return_code": None,
        "started_at": None,
        "completed_at": None,
    },
    "metadata": {},
}

INVALID_NON_NUMERIC_TID: dict[str, Any] = {
    "tid": "123456789012345678x",  # Contains letter
    "name": "test",
    "spec": {"type": "function", "function_target": "test:func"},
}

INVALID_MISSING_NAME: dict[str, Any] = {
    "tid": VALID_TEST_TID,
    # Missing name
    "spec": {"type": "function", "function_target": "test:func"},
    "io": {
        "inputs": {},
        "outputs": {"outbox": "test.out"},
        "control": {"ctrl_in": "test.in", "ctrl_out": "test.out"},
    },
    "state": {
        "status": "created",
        "return_code": None,
        "started_at": None,
        "completed_at": None,
    },
    "metadata": {},
}

INVALID_MISSING_SPEC: dict[str, Any] = {
    "tid": VALID_TEST_TID,
    "name": "test",
    # Missing spec
}

INVALID_MISSING_IO: dict[str, Any] = {
    "tid": VALID_TEST_TID,
    "name": "test",
    "spec": {"type": "function", "function_target": "test:func"},
    "state": {
        "status": "created",
        "return_code": None,
        "started_at": None,
        "completed_at": None,
    },
    "metadata": {},
    # Missing io
}

INVALID_MISSING_STATE: dict[str, Any] = {
    "tid": VALID_TEST_TID,
    "name": "test",
    "spec": {"type": "function", "function_target": "test:func"},
    "io": {
        "inputs": {},
        "outputs": {"outbox": "test.out"},
        "control": {"ctrl_in": "test.in", "ctrl_out": "test.out"},
    },
    "metadata": {},
    # Missing state
}

INVALID_MISSING_OUTBOX: dict[str, Any] = {
    "tid": VALID_TEST_TID,
    "name": "test",
    "spec": {"type": "function", "function_target": "test:func"},
    "io": {
        "inputs": {},
        "outputs": {"stdout": "test.stdout"},  # Missing outbox
        "control": {"ctrl_in": "test.in", "ctrl_out": "test.out"},
    },
    "state": {
        "status": "created",
        "return_code": None,
        "started_at": None,
        "completed_at": None,
    },
    "metadata": {},
}

INVALID_MISSING_CTRL_IN: dict[str, Any] = {
    "tid": VALID_TEST_TID,
    "name": "test",
    "spec": {"type": "function", "function_target": "test:func"},
    "io": {
        "inputs": {},
        "outputs": {"outbox": "test.out"},
        "control": {"ctrl_out": "test.out"},  # Missing ctrl_in
    },
    "state": {
        "status": "created",
        "return_code": None,
        "started_at": None,
        "completed_at": None,
    },
    "metadata": {},
}

INVALID_MISSING_CTRL_OUT: dict[str, Any] = {
    "tid": VALID_TEST_TID,
    "name": "test",
    "spec": {"type": "function", "function_target": "test:func"},
    "io": {
        "inputs": {},
        "outputs": {"outbox": "test.out"},
        "control": {"ctrl_in": "test.in"},  # Missing ctrl_out
    },
    "state": {
        "status": "created",
        "return_code": None,
        "started_at": None,
        "completed_at": None,
    },
    "metadata": {},
}

INVALID_MISSING_FUNCTION_TARGET: dict[str, Any] = {
    "tid": VALID_TEST_TID,
    "name": "test",
    "spec": {
        "type": "function",
        # Missing function_target
    },
    "io": {
        "inputs": {},
        "outputs": {"outbox": "test.out"},
        "control": {"ctrl_in": "test.in", "ctrl_out": "test.out"},
    },
    "state": {
        "status": "created",
        "return_code": None,
        "started_at": None,
        "completed_at": None,
    },
    "metadata": {},
}

INVALID_MISSING_PROCESS_TARGET: dict[str, Any] = {
    "tid": VALID_TEST_TID,
    "name": "test",
    "spec": {
        "type": "command",
        # Missing process_target
    },
    "io": {
        "inputs": {},
        "outputs": {"outbox": "test.out"},
        "control": {"ctrl_in": "test.in", "ctrl_out": "test.out"},
    },
    "state": {
        "status": "created",
        "return_code": None,
        "started_at": None,
        "completed_at": None,
    },
    "metadata": {},
}

INVALID_MEMORY_LIMIT: dict[str, Any] = {
    "tid": VALID_TEST_TID,
    "name": "test",
    "spec": {
        "type": "function",
        "function_target": "test:func",
        "limits": {
            "memory_mb": -100,  # Invalid negative
        },
    },
    "io": {
        "inputs": {},
        "outputs": {"outbox": "test.out"},
        "control": {"ctrl_in": "test.in", "ctrl_out": "test.out"},
    },
    "state": {
        "status": "created",
        "return_code": None,
        "started_at": None,
        "completed_at": None,
    },
    "metadata": {},
}

INVALID_CPU_LIMIT: dict[str, Any] = {
    "tid": VALID_TEST_TID,
    "name": "test",
    "spec": {
        "type": "function",
        "function_target": "test:func",
        "limits": {
            "cpu_percent": 150,  # Invalid > 100
        },
    },
    "io": {
        "inputs": {},
        "outputs": {"outbox": "test.out"},
        "control": {"ctrl_in": "test.in", "ctrl_out": "test.out"},
    },
    "state": {
        "status": "created",
        "return_code": None,
        "started_at": None,
        "completed_at": None,
    },
    "metadata": {},
}

INVALID_MISSING_METADATA: dict[str, Any] = {
    "tid": VALID_TEST_TID,
    "name": "test",
    "spec": {
        "type": "function",
        "function_target": "test:func",
    },
    "io": {
        "inputs": {},
        "outputs": {"outbox": "test.out"},
        "control": {"ctrl_in": "test.in", "ctrl_out": "test.out"},
    },
    "state": {
        "status": "created",
        "return_code": None,
        "started_at": None,
        "completed_at": None,
    },
    # Missing metadata section
}
