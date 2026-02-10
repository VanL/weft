# Implementation Plan

This document outlines the development roadmap for the Weft system, with a focus on delivering core functionality quickly while building a solid foundation.

## Implementation Schedule

### Phase 0: Manager Foundation (Days 1-2)

#### 0.1 CLI Framework and Implicit Manager Startup
**Files**: `weft/cli.py`, `weft/commands/run.py`
- [ ] Set up CLI framework with global options
- [ ] Ensure `weft run` implicitly starts a manager when needed
- [ ] Add context management (-d, -f flags)
- [ ] Error handling and exit code mapping

**Behavior**: Manager bootstraps automatically on first `weft run` (or via
explicit `weft worker start` for operators).
**Dependencies**: WeftContext, Manager, TaskSpec ✅

**Tests**: `tests/test_cli/test_run_bootstrap.py`
- [ ] Implicit manager startup
- [ ] Context directory handling
- [ ] Error exit codes (0=success, 1=error)

#### 0.2 Core Manager Components
**Files**: `weft/core/context.py`, `weft/core.manager.py`
- [ ] Implement WeftContext for directory scoping
- [ ] Implement Manager with run_forever target
- [ ] Add spawn_child method using message TID
- [ ] Implement task registry validation
- [ ] Add worker registration to weft.state.workers
- [ ] Handle control messages (STOP, STATUS, PING)

**CLI Integration**: `weft run` and `weft worker start` call Manager.run()
**Dependencies**: TaskSpec ✅, SimpleBroker Queue API

**Tests**: `tests/test_core/test_worker.py`, `tests/test_cli_integration/test_manager_startup.py`
- [ ] Worker lifecycle tests
- [ ] Spawn functionality tests
- [ ] Registry validation tests
- [ ] TID correlation tests
- [ ] CLI-Manager integration test (actual `weft run` startup)

### Phase 1: Core Infrastructure (Week 1)

#### 1.1 TaskSpec with Partial Immutability ✅
**File**: `weft/core/taskspec.py`
- [x] Implement partial immutability (spec/io frozen, state/metadata mutable)
- [x] Add limits subsection to spec
- [x] Implement state transition validation
- [x] Add convenience methods for state management
- [x] Optimize apply_defaults() with idempotency

**Tests**: `tests/taskspec/test_taskspec.py`
- [x] Immutability tests (spec cannot be modified after creation)
- [x] State transition tests (forward-only validation)
- [x] Limits validation tests
- [x] Convenience method tests

#### 1.2 Core Task Commands and Engine
**Files**: `weft/commands/run.py`, `weft/commands/status.py`,
`weft/core/tasks/base.py`, `weft/core/manager.py`

**CLI Commands**: `weft run`, `weft status`, `weft result`
**Dependencies**: Task, Client, TaskMonitor

**CLI Implementation**:
- [ ] `weft run COMMAND [--timeout N] [--wait] [--json]`
- [ ] `weft run --function MODULE:FUNC [--arg VALUE] [--kw KEY=VALUE]`
- [ ] `weft status [--json]`  
- [ ] `weft result TID [--timeout N] [--stream] [--json]`

**Core Components**:
- [ ] Implement unified reservation pattern (inbox → reserved → outbox)
- [ ] Integrate with SimpleBroker Queue API  
- [ ] Add BaseWatcher inheritance for queue monitoring
- [ ] Implement Client for submission/querying
- [ ] Implement state reporting to weft.log.tasks
- [ ] Add recovery from reserved queue on startup
- [ ] Implement process title management with setproctitle
- [ ] Add TID short form computation (last 10 digits)
- [ ] Register TID mappings to weft.state.tid_mappings queue
- [ ] Update process titles on state transitions

**CLI Integration**: Commands are thin wrappers around Client/TaskMonitor

**Tests**: `tests/test_cli/test_task_commands.py`, `tests/test_core/test_tasks.py`, `tests/test_cli_integration/test_run_status.py`
- [ ] CLI argument parsing and validation
- [ ] `weft run` creates tasks via Client
- [ ] `weft status` queries TaskMonitor correctly
- [ ] Exit codes: 0=success, 1=error, 2=not found, 124=timeout
- [ ] JSON output format consistency
- [ ] Reservation flow tests (move, process, clear)
- [ ] Failure handling tests (message stays in reserved)
- [ ] Recovery tests (resume from reserved)
- [ ] State reporting tests (weft.log.tasks updates)
- [ ] Process title format and update tests
- [ ] TID short form uniqueness tests
- [ ] TID mapping registration tests
- [ ] OS visibility tests (ps/top integration)

### Phase 2: Process Management and Monitoring (Week 2)

#### 2.1 Process Management Commands and Tools
**Files**: `weft/commands.py`, `weft/tools/process_tools.py`, `weft/tools/tid_tools.py`

**CLI Commands**: `weft task status`, `weft task kill`, `weft task stop`, `weft task tid`
**Dependencies**: ProcessManager, TIDResolver

**CLI Implementation**:
- [ ] `weft task status TID [--process] [--watch] [--json]`
- [ ] `weft task kill TID [--pattern PATTERN] [--force]`
- [ ] `weft task stop TID [--graceful]`
- [ ] `weft task tid SHORT_TID` / `weft task tid --pid PID`

**Implementation Notes**:
- Pattern-based `kill`/`stop` reuse `queue broadcast --pattern 'T*.ctrl_in'` to fan control messages to matching tasks, keeping the CLI thin over SimpleBroker’s selective broadcast.

**Tool Components**:
- [ ] Implement ProcessManager using ps/kill OS commands
- [ ] Process discovery via ps aux | grep weft-
- [ ] TID mapping and resolution (short ↔ full)
- [ ] Live monitoring with resource metrics
- [ ] Emergency task termination via OS signals

**CLI Integration**: Direct OS integration through system tools

**Tests**: `tests/test_cli/test_process_commands.py`, `tests/test_tools/test_process_tools.py`, `tests/test_cli_integration/test_ps_kill.py`
- [ ] CLI process listing and filtering
- [ ] TID resolution accuracy
- [ ] Kill command with proper exit codes
- [ ] Live monitoring functionality
- [ ] Emergency scenarios (kill failed tasks)

#### 2.2 Executor and Resource Monitor
**Files**: `weft/core/executor.py`, `weft/core.resource_monitor.py`

**CLI Integration**: Used by `weft run` for task execution

**Core Components**:
- [ ] Implement FunctionExecutor for Python callables
- [ ] Implement CommandExecutor for system processes
- [ ] Add timeout enforcement
- [ ] Integrate with reservation pattern
- [ ] Add stdin/stdout queue routing
- [ ] Implement subprocess process title inheritance
- [ ] Implement ResourceMonitor using psutil
- [ ] Check against spec.limits subsection
- [ ] Track current and maximum metrics
- [ ] Report violations to state
- [ ] Low-overhead monitoring for ephemeral tasks

**Tests**: `tests/test_core/test_executor.py`, `tests/test_core/test_monitor.py`
- [ ] Function execution with args/kwargs
- [ ] Command execution with environment
- [ ] Timeout enforcement
- [ ] Stream capture and queue writing
- [ ] Process title inheritance tests
- [ ] Limits enforcement from spec.limits
- [ ] Metric accuracy tests
- [ ] Violation detection
- [ ] Cleanup on task exit

### Phase 3: Worker Management and Queue Operations (Week 3)

#### 3.1 Worker Management Commands
**Files**: `weft/commands.py`, `weft/core.manager.py` (enhanced)

**CLI Commands**: `weft worker start/stop/list`, `weft worker status`
**Dependencies**: Enhanced Manager, WorkerLifecycle

**CLI Implementation**:
- [ ] `weft worker start [--type TYPE] [--registry FILE]`
- [ ] `weft worker stop TID [--force]`
- [ ] `weft worker list [--json]`
- [ ] `weft worker status TID [--json]`

**Worker Enhancement**:
- [ ] Worker hierarchy and specialization
- [ ] Worker registry management
- [ ] Task routing to appropriate workers
- [ ] Worker load balancing
- [ ] Automatic worker recovery

**CLI Integration**: Worker commands manage the recursive worker architecture

**Tests**: `tests/test_cli/test_worker_commands.py`, `tests/test_cli_integration/test_worker_lifecycle.py`
- [ ] Worker startup via CLI
- [ ] Worker termination and cleanup
- [ ] Worker registry validation
- [ ] Multi-worker coordination

#### 3.2 Queue Operations Commands
**Files**: `weft/commands.py` (queue subcommands)

**CLI Commands**: `weft queue read/write/peek/move/list/watch/broadcast/alias`
**Dependencies**: SimpleBroker Queue API, WeftContext

**CLI Implementation**:
- [x] `weft queue read QUEUE [--json] [--timeout N]`
- [x] `weft queue write QUEUE MESSAGE`
- [x] `weft queue peek QUEUE [--json] [--all]`
- [x] `weft queue move SOURCE TARGET [--all]`
- [x] `weft queue list [--pattern PATTERN] [--json]`
- [x] `weft queue watch QUEUE [--json]`
- [x] `weft queue broadcast MESSAGE [--pattern GLOB]`
- [x] `weft queue alias add ALIAS TARGET`
- [x] `weft queue alias remove ALIAS`
- [x] `weft queue alias list [--target QUEUE]`

**Queue Integration**:
- [x] Direct SimpleBroker delegation
- [x] Context-aware queue access
- [x] Safety validation and error handling
- [x] Emergency queue operations
- [x] Pattern-based fan-out for control messaging
- [x] Alias management for pipeline shortcuts

**CLI Integration**: Direct delegation to SimpleBroker with context management

### Phase 4: System Maintenance (Week 4)

**CLI Commands**: `weft system tidy`, `weft system dump`, `weft system load`

**Notes**:
- Maintenance operations are grouped under the `system` namespace.
- `system tidy` compacts the broker database.
- `system dump/load` are for backup/restore and debugging.

**Tests**: `tests/test_cli/test_queue_commands.py`, `tests/test_cli_integration/test_queue_operations.py`
- [x] All queue operations via CLI
- [x] Broadcast fan-out (match/no-match) semantics and exit codes
- [x] Alias add/list/remove, duplicate/cycle validation, cache refresh
- [x] Context isolation in queue access
- [x] Error handling and exit codes
- [x] Emergency recovery scenarios

#### 3.3 Enhanced Observability
**Files**: `weft/tools/observability.py`, `weft/commands.py` (enhanced status/list)

**CLI Enhancement**: Enhanced `weft status`, `weft list` with full observability

**Observability Components**:
- [ ] Implement weft.log.tasks aggregator
- [ ] Add TaskMonitor for querying state  
- [ ] Create log replay for state reconstruction
- [ ] Add summary/reporting utilities
- [ ] NO separate state database needed
- [ ] Implement TID lookup service from weft.state.tid_mappings
- [ ] Add process discovery tools (find tasks by pattern)
- [ ] Create OS integration utilities (ps/top wrappers)

**CLI Integration**: Status and list commands use enhanced observability

**Tests**: `tests/test_tools/test_observability.py`, `tests/test_cli_integration/test_enhanced_status.py`
- [ ] Log aggregation tests
- [ ] State query tests (by status, by tid)
- [ ] Replay consistency tests
- [ ] Performance with 200 tasks
- [ ] TID lookup service tests
- [ ] Process discovery tests
- [ ] OS command integration tests

### Phase 4: Spec Authoring Features (Week 4)

#### 4.1 Spec Authoring and Validation
**Files**: `weft/commands.py`, `weft/specs/pipelines.py`, `weft/specs/store.py`

**CLI Commands**: `weft spec create`, `weft spec list`, `weft spec show`, `weft spec delete`, `weft spec validate`, `weft spec generate`
**Dependencies**: PipelineBuilder, TaskSpecStore, enhanced Client

**CLI Implementation**:
- [ ] `weft spec create NAME --type task --file FILE`
- [ ] `weft spec create NAME --type pipeline --stage name:task`
- [ ] `weft spec list [--type task|pipeline] [--json]`
- [ ] `weft spec show NAME [--type task|pipeline]`
- [ ] `weft spec delete NAME [--type task|pipeline]`
- [ ] `weft spec validate FILE`
- [ ] `weft spec generate --type task|pipeline`

**Spec Components**:
- [ ] StreamAdapter for stdin/stdout to queues
- [ ] Pre-defined task registry for AI agents
- [ ] Pipeline builder for task chaining
- [ ] Task spec storage and instantiation
- [ ] Timeout handling for variable latencies

**CLI Integration**: Advanced workflows through structured commands

**Tests**: `tests/test_cli/test_spec_commands.py`, `tests/test_integration/`, `tests/test_cli_integration/test_pipelines.py`
- [ ] Pipeline creation and execution via CLI
- [ ] Task spec management lifecycle
- [ ] AI agent task restrictions
- [ ] Pipeline execution tests
- [ ] Variable latency handling

#### 4.2 Complete CLI Integration and Polish

**OS Integration Tools** (`weft/tools/`):
```python
# weft/tools/process_tools.py
class ProcessManager:
    def find_tasks(pattern: str = None) -> list[dict]
        """Find all weft tasks via ps, optionally filtered by pattern."""
    
    def kill_task(tid_short: str) -> bool
        """Kill task by short TID using OS signals."""
    
    def get_task_tree(tid: str) -> dict
        """Get process tree for task and subprocesses."""
    
    def monitor_tasks(interval: float = 1.0) -> Iterator[list[dict]]
        """Live monitoring of all weft tasks."""

# weft/tools/tid_tools.py  
class TIDResolver:
    def resolve_short_tid(tid_short: str) -> str
        """Resolve short TID to full TID from mappings."""
    
    def find_tid_by_pid(pid: int) -> str
        """Find TID for given process ID."""
    
    def cleanup_stale_mappings(max_age: int = 86400) -> int
        """Remove old TID mappings."""
```

**Complete CLI Reference**:
```bash
# Bootstrap and system management
weft worker start [--config FILE] [--recover]

# Core task management  
weft run COMMAND [--timeout N] [--wait] [--json]
weft run --function MODULE:FUNC [--arg VALUE] [--kw KEY=VALUE]
weft run --spec NAME|PATH [--timeout N] [--wait] [--json]
weft run --pipeline NAME|PATH [--timeout N] [--wait] [--json]
weft status [--json]
weft result TID [--timeout N] [--stream] [--json]
weft list [--stats] [--status STATUS] [--json]

# Process management (no escaping needed!)
weft task status TID [--process] [--watch] [--json]
weft task kill TID [--pattern PATTERN] [--force]
weft task stop TID [--graceful]

# TID management  
weft task tid SHORT_TID           # Lookup full TID
weft task tid --pid PID           # Find TID for process

# Worker management
weft worker start [--type TYPE] [--registry FILE]
weft worker stop TID [--force]
weft worker list [--json]
weft worker status TID [--json]

# Queue operations (delegates to SimpleBroker)
weft queue read QUEUE [--json] [--timeout N]
weft queue write QUEUE MESSAGE
weft queue peek QUEUE [--json] [--all]
weft queue move SOURCE TARGET [--all]
weft queue list [--pattern PATTERN] [--json]
weft queue watch QUEUE [--json]

# Spec management
weft spec create NAME --type task --file FILE
weft spec create NAME --type pipeline --stage name:task
weft spec list [--type task|pipeline] [--json]
weft spec show NAME [--type task|pipeline]
weft spec delete NAME [--type task|pipeline]
weft spec validate FILE
weft spec generate --type task|pipeline
cat TASKS.jsonl | xargs -P4 -n1 weft run --spec

# Emergency operations (shell-friendly!)
ps aux | grep weft-               # Find all weft tasks
pkill -f "weft-.*:failed"         # Kill failed tasks
pgrep -f "weft-" -a               # List with arguments

# Easy parsing with standard tools
ps aux | grep weft- | cut -d- -f2 | cut -d: -f1     # Extract TIDs
ps aux | grep weft- | awk -F'[-:]' '{print $2}'     # Same with awk
```

**Examples**: `examples/`
- [ ] Unix pipeline example (grep → sort → uniq)
- [ ] AI agent example (prompt → LLM → parse)
- [ ] Mixed pipeline (file → AI → command)
- [ ] Failure recovery scenarios
- [ ] Process management examples
- [ ] Emergency cleanup scripts

## Module Architecture

### Proposed File Structure

Following SimpleBroker's successful patterns, Weft uses a structured approach that separates concerns while keeping related functionality together:

```
weft/
├── __init__.py                    # Main package exports  
├── __main__.py                    # Entry point for `python -m weft`
├── _constants.py                  # Internal constants
├── cli.py                         # CLI framework (Click-based)
├── commands.py                    # CLI command implementations
├── helpers.py                     # Utility functions
├── exceptions.py                  # Custom exceptions

├── core/                          # Core system components
│   ├── __init__.py               # Core exports
│   ├── taskspec.py               # TaskSpec (already exists) ✅
│   ├── tasks.py                  # Task execution engine 
│   ├── worker.py                 # Worker/recursive task implementation
│   ├── executor.py               # Target execution (function/command)
│   ├── monitor.py                # Resource monitoring with psutil
│   ├── context.py                # WeftContext and directory scoping
│   └── manager.py                # Client for submission/querying

├── tools/                         # OS integration and process tools
│   ├── __init__.py               # Tools exports
│   ├── process_tools.py          # Process discovery, management via ps/kill
│   ├── tid_tools.py              # TID resolution, short<->full mapping
│   └── observability.py         # Log aggregation, state querying

├── specs/                         # Task spec authoring utilities
│   ├── __init__.py               # Spec exports
│   ├── streams.py                # Stream adapters (stdin/stdout <-> queues)
│   ├── pipelines.py              # Task pipeline builders
│   └── store.py                  # Task spec storage and instantiation

tests/                             # Test suite (mirrors module structure)
├── __init__.py
├── fixtures/                     # Test fixtures and data
├── test_core/                    # Core component tests
├── test_tools/                   # Tools tests
├── test_spec/                    # Spec-related integration tests
└── test_cli/                     # CLI tests
```

### Module Responsibilities

**Root Package (`weft/`)**:
- `cli.py`: Click-based command framework
- `commands.py`: Individual CLI command implementations  
- `helpers.py`: Cross-cutting utility functions
- `exceptions.py`: Custom exception classes

**Core Package (`weft/core/`)**:
- `taskspec.py`: TaskSpec data model and validation ✅
- `tasks.py`: Task class extending SimpleBroker's MultiQueueWatcher
- `worker.py`: Manager implementation for recursive architecture
- `executor.py`: FunctionExecutor and CommandExecutor for target execution
- `monitor.py`: ResourceMonitor using psutil for limits enforcement
- `context.py`: WeftContext for directory-based scoping
- `manager.py`: Client for task submission and querying

**Tools Package (`weft/tools/`)**:
- `process_tools.py`: Process discovery and management via OS commands
- `tid_tools.py`: TID resolution between short and full forms
- `observability.py`: Log aggregation and system state querying

**Spec Package (`weft/specs/`)**:
- `streams.py`: StreamAdapter for stdin/stdout to queue routing
- `pipelines.py`: Task pipeline creation and management
- `store.py`: Task spec storage and instantiation

### Design Rationale

This structure follows SimpleBroker's successful patterns while organizing Weft's more complex functionality:

1. **Flat Root**: Simple CLI and utility functions stay at the root level
2. **Core Separation**: Complex system components grouped under `core/`
3. **Tool Isolation**: OS integration tools separated from core logic
4. **Spec Boundary**: Task spec authoring utilities separated from core logic
5. **Test Mirroring**: Test structure mirrors source for easy navigation

### Import Patterns

```python
# Main public API
from weft import TaskSpec, Task, WeftContext

# Core components
from weft.core import Client, ResourceMonitor
from weft.core.manager import Manager

# Tools for system administration
from weft.tools import ProcessManager, TIDResolver
from weft.tools.observability import TaskMonitor

# Spec utilities
from weft.specs import StreamAdapter, PipelineBuilder
from weft.specs.store import TaskSpecStore
```

## Dependencies and Installation

### Core Dependencies
```toml
[tool.poetry.dependencies]
python = "^3.8"
simplebroker = "^1.0"  # Queue system
pydantic = "^2.0"      # TaskSpec validation
psutil = "^5.9"        # Resource monitoring

[tool.poetry.extras]
observability = ["setproctitle"]  # Process titles (strongly recommended)
dev = ["pytest", "pytest-asyncio", "hypothesis"]
```

### Installation
```bash
# Basic installation
pip install weft

# With observability features (recommended)
pip install weft[observability]

# Development installation
pip install -e .[dev,observability]
```

### Platform Support
| Platform | Core Features | Process Titles | Subprocess Titles |
|----------|--------------|----------------|-------------------|
| Linux | ✅ Full | ✅ Full | ✅ Full (preexec_fn) |
| macOS | ✅ Full | ✅ Full | ✅ Full (preexec_fn) |
| Windows | ✅ Full | ✅ Full | ⚠️ Wrapper script |
| FreeBSD | ✅ Full | ✅ Full | ✅ Full (preexec_fn) |

## Development Workflow

### Daily Development Cycle

1. **Morning Standup** (5 min):
   - Review previous day's progress
   - Identify current day's target
   - Check for any blocking issues

2. **Implementation** (3-4 hours):
   - Focus on single component at a time
   - Write tests first (TDD approach)
   - Implement minimal viable functionality

3. **Integration Testing** (1 hour):
   - Run relevant test suites
   - Fix immediate integration issues
   - Update documentation

4. **End of Day Review** (15 min):
   - Commit working code
   - Update task tracking
   - Plan next day's work

### Quality Gates

**Phase Completion Criteria**:
- [ ] All planned features implemented
- [ ] Test coverage >= 90%
- [ ] All tests passing on target platforms
- [ ] Documentation updated
- [ ] Performance targets met (where applicable)

**Weekly Reviews**:
- Code review for major components
- Architecture review for design decisions
- Performance benchmarking
- Security review for new functionality

### Risk Mitigation

**Technical Risks**:

1. **SimpleBroker Integration Issues**
   - Mitigation: Early prototyping with SimpleBroker
   - Fallback: Implement minimal queue abstraction

2. **Cross-Platform Process Title Issues**
   - Mitigation: Graceful degradation without setproctitle
   - Fallback: Log-only observability

3. **Performance Under Load**
   - Mitigation: Continuous benchmarking
   - Fallback: Configurable polling intervals

4. **Resource Monitoring Accuracy**
   - Mitigation: Platform-specific testing
   - Fallback: Conservative limit enforcement

**Schedule Risks**:

1. **Scope Creep**
   - Mitigation: Strict adherence to MVP definition
   - Defer non-essential features to future releases

2. **Integration Complexity**
   - Mitigation: Early integration testing
   - Parallel development where possible

3. **Platform Issues**
   - Mitigation: Develop on primary platform first
   - Cross-platform testing in CI/CD

## Future Enhancements (Post-v1.0)

### Performance Optimizations
- [ ] Connection pooling for SimpleBroker
- [ ] Batch queue operations
- [ ] Memory-mapped queue files
- [ ] Zero-copy message passing

### Advanced Features
- [ ] Distributed task execution
- [ ] Container/cgroup integration
- [ ] Web dashboard for monitoring
- [ ] Metrics export (Prometheus)
- [ ] Task scheduling (cron-like)

### Security Enhancements
- [ ] Task execution sandboxing
- [ ] Resource quotas per user
- [ ] Audit logging
- [ ] Encrypted queue communication

### Developer Experience
- [ ] VS Code extension
- [ ] Task debugging tools
- [ ] Visual pipeline builder
- [ ] Performance profiler

## Success Metrics

### Technical Metrics
- **Correctness**: Zero data loss, all invariants maintained
- **Performance**: 100 tasks/sec creation, 1000 msg/sec throughput
- **Reliability**: 99.9% task completion rate
- **Observability**: 100% task visibility in OS tools

### Quality Metrics
- **Test Coverage**: ≥90% line coverage
- **Code Quality**: Clean architecture, minimal technical debt
- **Documentation**: Complete API and user documentation
- **Platform Support**: Full functionality on Linux/macOS/Windows

### User Experience Metrics
- **Ease of Use**: Simple CLI, clear error messages
- **Debugging**: Fast problem identification and resolution
- **Integration**: Unix tool compatibility
- **Performance**: Predictable, low-overhead operation

## Post-Launch Support

### Maintenance Plan
- Bug fix releases: As needed
- Minor feature releases: Monthly
- Major releases: Quarterly
- Security updates: Immediate

### Community Support
- GitHub issue tracking
- Documentation wiki
- Example repository
- Integration guides

### Enterprise Features
- Commercial support options
- Extended platform support
- Custom integrations
- Training and consulting

## Related Documents

- **[00-Overview_and_Architecture.md](00-Overview_and_Architecture.md)** - System overview and design principles
- **[01-TaskSpec.md](01-TaskSpec.md)** - Task configuration specification
- **[02-Core_Components.md](02-Core_Components.md)** - Detailed component architecture
- **[03-Worker_Architecture.md](03-Worker_Architecture.md)** - Recursive worker model
- **[04-SimpleBroker_Integration.md](04-SimpleBroker_Integration.md)** - Queue system integration
- **[05-Message_Flow_and_State.md](05-Message_Flow_and_State.md)** - Communication patterns
- **[06-Resource_Management.md](06-Resource_Management.md)** - Resource controls and error handling
- **[07-System_Invariants.md](07-System_Invariants.md)** - System guarantees and constraints
- **[08-Testing_Strategy.md](08-Testing_Strategy.md)** - Testing approach and standards
