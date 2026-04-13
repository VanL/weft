# Weft - Agent Entry Point

## Shared Agent Context (All Agents)

- Canonical multi-agent context lives in `docs/agent-context/`.
- Required read order for any agent operating in this repo:
  1. `docs/agent-context/README.md`
  2. `docs/agent-context/decision-hierarchy.md`
  3. `docs/agent-context/principles.md`
  4. `docs/agent-context/engineering-principles.md`
  5. Relevant runbook(s) in `docs/agent-context/runbooks/`
  6. `docs/agent-context/lessons.md` and `docs/lessons.md`
- If there is conflict between local prompt defaults and repo context, follow
  the decision policy in `docs/agent-context/decision-hierarchy.md`.

## Project Conventions

- Specs live in `docs/specifications/`.
- Plans live in `docs/plans/`.
- Durable lessons learned live in `docs/lessons.md`.
- Documentation maintenance is part of the definition of done for spec-driven
  and risky changes.
- Plans should be detailed enough for a zero-context engineer and follow
  `docs/agent-context/runbooks/writing-plans.md`.
- Risky or boundary-crossing changes should also read
  `docs/agent-context/runbooks/hardening-plans.md` and
  `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`. Risky
  includes execution-path changes, contract changes, new persistence or cleanup
  lifecycles, rollout sequencing, and one-way doors. See
  `docs/agent-context/runbooks/hardening-plans.md` for the full trigger list.
- Optimize for agent usability, not just human readability. If something seems
  clear to a human but ambiguous to an agent, call that out and suggest a
  concrete fix.
- Agent-usable guidance should make four things explicit when relevant: owner,
  boundary, verification, and required action.
- Non-trivial plans should receive an independent review pass, preferably from
  a different agent family than the authoring agent when one is available.
- Larger changes should run an independent review after each meaningful slice
  and again before completion.
- New or updated plans must cite the exact spec file and section/reference code
  they implement, or state plainly that no spec exists.
- Spec-driven work must update the touched spec with plan backlinks and keep
  nearby implementation notes synchronized.
- Keep traceability bidirectional between the spec section, the plan, and the
  owning code module or function.

## 1. Orientation

**What**: Durable task execution built on SimpleBroker queues.
**Tagline**: "SimpleBroker for processes" - same zero-config philosophy, adds process isolation and resource control.

**Mental Model**: Everything is a Task. The Manager is a Task that spawns other Tasks. Queues are the only communication channel. State lives in queues, not databases.

**When to use weft**:
- Tasks that might outlive your session
- Parallel work with result collection
- Multi-agent coordination via queues
- Long-running processes with resource limits
- Observable, recoverable workflows

**Current state**: Core working. CLI working. Tests passing.

## 1.1 If You're New Here (Read This First)

You are a skilled developer with limited context. This project is a durable
task runner built on SimpleBroker. Keep changes small, grounded in the specs,
and tested. Avoid "clever" abstractions.

**Where to start**
1. Read `docs/agent-context/README.md`.
2. Read `docs/agent-context/decision-hierarchy.md`.
3. Read `docs/agent-context/principles.md`.
4. Read `docs/specifications/00-Overview_and_Architecture.md` for the mental model.
5. Read `docs/specifications/02-TaskSpec.md` for the TaskSpec schema.
6. Read `docs/specifications/05-Message_Flow_and_State.md` for queue flows.
7. Check `docs/specifications/07-System_Invariants.md` before touching core logic.
8. Use `README.md` for usage examples and CLI behavior.

**Key principle**: The specs are the source of truth. Code must align to them.

## 2. Architecture

```
User ──► weft run ──► Manager (background) ──► Consumer (child process)
                           │                         │
                   weft.spawn.requests        T{tid}.inbox/outbox/ctrl
                           │                         │
                   weft.log.tasks ◄──────────────────┘
```

**Key files**:
| File | Purpose |
|------|---------|
| `weft/cli.py` | Entry point, command routing |
| `weft/commands/run.py` | Main command |
| `weft/core/taskspec.py` | TaskSpec schema (source of truth) |
| `weft/core/manager.py` | Background manager |
| `weft/core/tasks/consumer.py` | Executes work |
| `weft/core/tasks/base.py` | Queue wiring, control handling |
| `weft/_constants.py` | All constants and env vars |
| `docs/specifications/00-Quick_Reference.md` | Queue names, states, control messages |

**Queue naming**:
```
Per-task:           T{tid}.inbox, T{tid}.reserved, T{tid}.outbox, T{tid}.ctrl_in, T{tid}.ctrl_out
Global logs:        weft.log.tasks, weft.spawn.requests
Manager:            weft.manager.ctrl_in, weft.manager.ctrl_out, weft.manager.outbox
Runtime state:      weft.state.managers, weft.state.tid_mappings, weft.state.streaming
```

## 3. Invariants

**NEVER break**:
- TID is a 64-bit SimpleBroker hybrid timestamp (microseconds + logical counter), format-compatible with `time.time_ns()` and typically 19 digits; immutable after creation
- State transitions are forward-only (created → running → completed/failed/timeout/killed)
- Reserved queue policy must be honored (keep/requeue/clear)
- Process titles must be shell-safe: `weft-{context_short}-{tid_short}:{name}:{status}[:details]`
- `spec` and `io` sections are immutable after TaskSpec creation

**Safe to change**:
- CLI command names/flags (with migration notes)
- Default timeout/limit values
- Log message formats
- Metadata fields

**Gotchas**:
- Manager must be running for `weft run` to work (auto-starts if needed)
- SimpleBroker Queue connections can't cross fork() - always use spawn context
- Tests use WeftTestHarness (`tests/helpers/weft_harness.py`) - always clean up
- `weft.state.*` queues are runtime-only, excluded from dumps

## 4. House Style

This section defines how code should be written to match existing patterns.

### 4.1 File Organization

```
weft/
├── _constants.py       # ALL constants, env vars, config loading
├── _exceptions.py      # Custom exceptions (if needed)
├── cli.py              # Entry point
├── helpers.py          # Shared utilities
├── commands/           # CLI command handlers (one file per command group)
├── core/               # Business logic
│   ├── taskspec.py     # Data models
│   └── tasks/          # Task implementations
└── shell/              # Subprocess helpers
```

**Conventions**:
- `_constants.py` is the single source for all constants and environment variable loading
- `_exceptions.py` for custom exceptions (dual-inherit from stdlib + domain base)
- `helpers.py` for utility functions shared across modules
- One command file per CLI command group
- `core/` for business logic, separate from CLI concerns

### 4.2 Imports

```python
"""Module docstring with spec references [CC-1], [TS-2]."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, Field, field_validator
from simplebroker import Queue

from weft._constants import (
    DEFAULT_TIMEOUT,
    WEFT_GLOBAL_LOG_QUEUE,
    load_config,
)
from weft.core.taskspec import TaskSpec
```

**Rules**:
- `from __future__ import annotations` in every file (enables forward references)
- All imports at the top of the module (no late imports; avoid import loops by design)
- Group: stdlib → third-party → local, alphabetized within groups
- Use `collections.abc` for abstract types (Callable, Iterator, Mapping, Sequence)
- Use `Path` from pathlib, never `os.path`
- Prefer relative imports inside a package (e.g. `from ._constants import ...`); avoid cross-package relative imports
- No wildcard imports except in `__init__.py` for re-exports

### 4.3 Type Hints

**Use modern syntax**:
```python
# Good
def process(data: str | None = None) -> dict[str, Any]: ...
def fetch(ids: list[int]) -> Sequence[Result]: ...
callback: Callable[[str, int], bool]

# Bad
def process(data: Optional[str] = None) -> Dict[str, Any]: ...
from typing import List, Dict, Optional  # Don't import these
```

**Rules**:
- `X | None` not `Optional[X]`
- `list[T]`, `dict[K, V]`, `set[T]` not `List`, `Dict`, `Set`
- `Callable`, `Iterator`, `Mapping`, `Sequence` from `collections.abc`
- `Final[T]` for constants
- `Literal["a", "b"]` for constrained strings
- All functions must have complete type annotations (enforced by mypy)

### 4.4 Constants

All constants live in `_constants.py`:

```python
from typing import Any, Final

# Grouped by purpose with comments
# --- Exit codes ---
EXIT_SUCCESS: Final[int] = 0
EXIT_ERROR: Final[int] = 1
EXIT_NOT_FOUND: Final[int] = 2

# --- Queue naming ---
QUEUE_INBOX_SUFFIX: Final[str] = "inbox"
QUEUE_OUTBOX_SUFFIX: Final[str] = "outbox"

# --- Defaults (with docstrings for non-obvious values) ---
DEFAULT_TIMEOUT: Final[float | None] = None
"""Default timeout in seconds. None means no timeout."""

DEFAULT_MEMORY_MB: Final[int] = 512
"""Default memory limit for tasks."""


def load_config() -> dict[str, Any]:
    """Load configuration from environment variables.

    Environment Variables:
        WEFT_DEBUG: Enable debug logging (default: false)
        WEFT_DB_PATH: Database path (default: ~/.weft/weft.db)

    Returns:
        Configuration dictionary with resolved values.
    """
    return {
        "debug": os.environ.get("WEFT_DEBUG", "").lower() in ("1", "true"),
        "db_path": os.environ.get("WEFT_DB_PATH", str(Path.home() / ".weft" / "weft.db")),
    }
```

### 4.5 Docstrings

Structured docstrings with spec references:

```python
def resolve_command(command: str, *, search_path: str | None = None) -> str:
    """Resolve a CLI command to its fully qualified executable path.

    Args:
        command: Name of the command to resolve.
        search_path: Optional PATH string to use instead of system PATH.

    Returns:
        Absolute path to the executable.

    Raises:
        ValueError: If command is empty.
        CommandNotFoundError: If command cannot be located.

    Example:
        >>> resolve_command("python")
        '/usr/bin/python'

    Note:
        This wraps shutil.which to centralize command resolution.

    Spec: [CLI-1.1.1]
    """
```

**Module docstrings**:
```python
"""Task consumer implementation.

This module implements the Consumer class which executes work items
from task queues. It handles process lifecycle, resource limits,
and result collection.

Spec references:
- docs/specifications/01-Core_Components.md [CC-2.1], [CC-2.2]
- docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-3]
"""
```

**Traceability rules**:
- New or materially changed modules should keep module docstrings pointing to
  the governing spec files and section codes.
- When a single function or method owns a spec boundary, include a `Spec:`
  reference in that docstring rather than relying only on the module docstring.
- If implementation ownership changes, update both the spec's nearby
  `Implementation mapping` notes and the touched code docstrings in the same
  change.

### 4.6 Data Modeling

**Pydantic for validated structured data**:
```python
from pydantic import BaseModel, Field, field_validator, model_validator

class LimitsSection(BaseModel):
    """Resource limits for task execution (Spec: [CC-1])."""

    memory_mb: int | None = Field(None, ge=1, description="Memory limit in MB")
    cpu_percent: int | None = Field(None, ge=1, le=100, description="CPU limit")
    timeout: float | None = Field(None, gt=0, description="Timeout in seconds")

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError("timeout must be positive")
        return v

    @model_validator(mode="after")
    def validate_limits(self) -> LimitsSection:
        """Cross-field validation."""
        if self.memory_mb and self.memory_mb < 64:
            raise ValueError("memory_mb must be at least 64")
        return self
```

**Dataclasses for simple immutable values**:
```python
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Result of a managed process execution."""

    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration: float

    @property
    def success(self) -> bool:
        """Whether the process exited successfully."""
        return self.returncode == 0
```

**Protocol for duck-typed interfaces**:
```python
from typing import Protocol

class SQLRunner(Protocol):
    """Interface for SQL execution."""

    def run(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
        *,
        fetch: bool = False,
    ) -> Iterable[tuple[Any, ...]]:
        """Execute SQL and optionally fetch results."""
        ...
```

### 4.7 Error Handling

**Custom exceptions with dual inheritance**:
```python
class WeftError(Exception):
    """Base exception for all Weft errors."""
    pass


class CommandNotFoundError(WeftError, FileNotFoundError):
    """Raised when a CLI command cannot be resolved (Spec: [CLI-1.1.1])."""

    def __init__(self, command: str, *, search_path: str | None = None) -> None:
        message = f"Unable to locate command '{command}'"
        if search_path is not None:
            message += f" using search path '{search_path}'"
        super().__init__(message)
        self.command = command
        self.search_path = search_path


class TaskTimeoutError(WeftError, TimeoutError):
    """Raised when a task exceeds its timeout limit."""

    def __init__(self, tid: str, timeout: float) -> None:
        super().__init__(f"Task {tid} exceeded timeout of {timeout}s")
        self.tid = tid
        self.timeout = timeout
```

**Defensive exception handling**:
```python
# Mark defensive catches with pragma
try:
    risky_operation()
except Exception:  # pragma: no cover - defensive
    logger.debug("Operation failed", exc_info=True)
    # Continue with fallback

# Specific exception handling preferred
try:
    data = json.loads(message)
except json.JSONDecodeError:
    return {"raw": message}  # Fallback to raw
```

### 4.8 Testing

**File organization**:
```
tests/
├── conftest.py          # Shared fixtures
├── cli/
│   └── test_commands.py
├── core/
│   ├── test_taskspec.py
│   └── test_manager.py
└── tasks/
    └── test_consumer.py
```

**Fixture patterns**:
```python
# conftest.py
import pytest
from pathlib import Path

@pytest.fixture
def unique_tid() -> str:
    """Generate a unique task ID for testing."""
    return str(time.time_ns())


@pytest.fixture
def broker_env(tmp_path: Path) -> tuple[str, Callable[[str], Queue]]:
    """Provide isolated broker environment."""
    db_path = str(tmp_path / "test.db")

    def queue_factory(name: str) -> Queue:
        return Queue(name, db_path=db_path)

    return db_path, queue_factory
```

**Test function style**:
```python
def test_consumer_writes_result_to_outbox_on_success(
    broker_env: tuple[str, Callable[[str], Queue]],
    unique_tid: str,
) -> None:
    """Test that successful execution writes result to outbox.

    Verifies:
    - Function target executes correctly
    - Result is written to T{tid}.outbox
    - State transitions to 'completed'
    """
    db_path, queue_factory = broker_env
    outbox = queue_factory(f"T{unique_tid}.outbox")

    # Arrange
    spec = create_test_spec(tid=unique_tid, target="echo hello")

    # Act
    consumer = Consumer(spec, db_path=db_path)
    consumer.run()

    # Assert
    result = outbox.read(timeout=1.0)
    assert result is not None
    assert "hello" in result


@pytest.mark.slow
def test_timeout_kills_long_running_process(broker_env, unique_tid):
    """Test that processes exceeding timeout are terminated."""
    ...


@pytest.mark.parametrize("exit_code,expected_status", [
    (0, "completed"),
    (1, "failed"),
    (137, "killed"),
])
def test_exit_code_maps_to_status(exit_code, expected_status, broker_env):
    """Test exit code to status mapping."""
    ...
```

### 4.9 Logging

```python
import logging

logger = logging.getLogger(__name__)

# Use structured context
logger.info("Task started", extra={"tid": tid, "command": command})
logger.debug("Processing message", extra={"queue": queue_name, "size": len(msg)})
logger.error("Task failed", extra={"tid": tid, "error": str(e)}, exc_info=True)
```

### 4.10 Context Managers

```python
from contextlib import contextmanager
from collections.abc import Iterator

@contextmanager
def mutations_allowed(obj: TaskSpec) -> Iterator[None]:
    """Temporarily allow mutations on a frozen object."""
    object.__setattr__(obj, "_allow_mutation", True)
    try:
        yield
    finally:
        object.__setattr__(obj, "_allow_mutation", False)
```

### 4.11 Security

**Path validation** (follow SimpleBroker patterns):
```python
def validate_safe_path(path: str) -> str:
    """Validate path is safe for use.

    Raises:
        ValueError: If path contains dangerous patterns.
    """
    # Reject traversal
    if ".." in path:
        raise ValueError(f"Path contains traversal: {path}")

    # Reject shell metacharacters
    dangerous = set(';&|`$(){}[]<>"\'\\')
    if any(c in path for c in dangerous):
        raise ValueError(f"Path contains shell metacharacters: {path}")

    return path
```

**Input validation at boundaries**:
```python
# Validate user input at CLI boundary
def handle_command(user_input: str) -> None:
    # Validate early
    if not user_input.strip():
        raise ValueError("Command cannot be empty")

    command = validate_safe_path(user_input)
    # Now safe to use
```

## 5. Common Tasks

### For Development

**Run tests**:
```bash
. ./.envrc                       # Or `direnv allow` if you use direnv
uv sync --all-extras             # Install dev tools into the repo env
./.venv/bin/python -m pytest     # Fast tests only
./.venv/bin/python -m pytest -m ""  # All tests including slow
./.venv/bin/python -m pytest tests/cli/ -v  # Specific directory
./.venv/bin/mypy weft            # Type check
./.venv/bin/ruff check weft      # Lint
```

Do not assume `pytest`, `mypy`, or `ruff` are installed globally. Load
`.envrc` first, then use the in-repo virtualenv binaries so verification uses
the repo-managed toolchain and local `bin/` wiring.

## 5.1 How to Work (Bite‑Sized Tasks)

**Planning**
- Break work into 2–5 small steps. Each step should be testable or reviewable.
- If a task is large, plan a “thin slice” first (smallest end‑to‑end behavior).
- For work touching 3+ files, cross-cutting behavior, or a new execution path,
  write/update a plan in `docs/plans/` first.

**Implementation loop**
1. Read the spec + current code that matches your change.
2. Identify the smallest testable change.
3. Write/adjust a test if you can (TDD is preferred, but be pragmatic).
4. Implement the change.
5. Run the smallest relevant test(s), then expand if needed.

**DRY / YAGNI**
- Do not introduce new abstractions unless you are forced to duplicate logic.
- Avoid “future‑proofing” unless the spec explicitly requires it.

## 5.2 Test Design (If You’re Unsure)

**Prefer tests that: **
- Assert observable behavior (queue messages, state events, output).
- Use `WeftTestHarness` (`tests/helpers/weft_harness.py`) for isolation.
- Minimize timing flakiness (use deterministic inputs when possible).

**Avoid tests that:**
- Depend on timing sleeps for correctness.
- Reach into private internals unless necessary.
- Require network access or external services.

**Common patterns**
- Task lifecycle: enqueue → reserved → outbox → state log.
- Control messages: send STOP/PING/STATUS → verify ctrl_out + state log.
- Resource limits: configure limit → trigger → verify terminal state + error.

**Add a CLI command**:
1. Create `weft/commands/mycommand.py`
2. Add to `weft/commands/__init__.py`
3. Register in `weft/cli.py`
4. Add test in `tests/cli/test_cli_mycommand.py`

**Debug a task**:
```bash
weft status                      # System overview
weft task list --status failed   # Find failed tasks
weft queue peek T{tid}.reserved  # Check failed messages
weft queue peek weft.log.tasks   # Check state events
```

### For Using Weft

**Basic execution**:
```bash
weft run echo "hello"                    # Run and wait
weft run --no-wait long-task.sh          # Fire and forget, returns TID
weft result <tid>                        # Get output when ready
weft status                              # What's happening?
```

**With limits**:
```bash
weft run --timeout 60 --memory 512 --cpu 50 heavy-task
```

**Parallel fan-out**:
```bash
for file in *.py; do
  weft run --no-wait --tag file=$file analyze.py "$file"
done
weft task list --json | jq '.[] | select(.status=="completed")'
```

## 6. CLI Quick Reference

| Command | Purpose |
|---------|---------|
| `weft run CMD` | Execute a command |
| `weft run --spec NAME` | Execute stored task spec |
| `weft status` | System overview |
| `weft task list` | List tasks |
| `weft task status TID` | Inspect one task |
| `weft result TID` | Get task output |
| `weft task stop TID` | Graceful stop |
| `weft task kill TID` | Force terminate |
| `weft queue read/write/peek/move/list/watch` | Direct queue ops |
| `weft manager list/start/stop/serve` | Manager lifecycle |
| `weft spec create/list/show/delete/validate` | Manage stored specs |
| `weft system tidy/dump/load` | Maintenance |

**Exit codes**: 0=success, 1=error, 2=not found, 124=timeout

## 7. Handoff Protocol

**Before claiming "done"**:
- [ ] Tests pass (`uv run pytest`)
- [ ] Type check passes (`uv run mypy weft`)
- [ ] Lint passes (`uv run ruff check weft`)
- [ ] Changes are minimal (no drive-by refactoring)

**When stuck**:
- State what you tried
- State what failed
- Ask specific question

**When handing off**:
- Summarize what was done
- List what remains
- Note any gotchas discovered
- Add or update `docs/lessons.md` if a correction exposed a repeated pattern

## 8. Agent Boundaries

**Do freely**:
- Read any file
- Run tests
- Make targeted edits
- Add new test files

**Ask first**:
- Architectural changes
- New dependencies
- Changes to public API
- Deleting files

**Don't do**:
- Push to git
- Modify CI/CD
- Change project config without discussion
- "Improve" unrelated code

## 9. Key Design Decisions

**Why SimpleBroker, not Redis/RabbitMQ?**
Zero config, SQLite-backed, same philosophy. Weft inherits "just works."

**Why spawn context, not fork?**
Clean process state. No inherited file descriptors or connections.

**Why queue-based state, not database?**
Single source of truth. Event-sourced. SimpleBroker handles persistence.

**Why partial immutability in TaskSpec?**
Prevent accidental modification of execution config while allowing state updates.

**Why process titles?**
Observable with standard Unix tools (`ps`, `pgrep`, `pkill`). No custom tooling required.

---

*See `docs/agent-context/` for shared durable agent guidance.*
*See `docs/specifications/` for detailed implementation specs.*
*See `docs/specifications/00-Quick_Reference.md` for queue names, states, and control messages.*
