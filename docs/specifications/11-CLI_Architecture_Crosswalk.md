# CLI-Architecture Crosswalk

This document maps every CLI command to its corresponding system components, establishing the development dependencies between the CLI interface and the underlying architecture. The CLI and system components must be developed in lockstep to ensure proper integration.

## Executive Summary

The Weft CLI serves as the primary interface to all system functionality. Each CLI command directly corresponds to specific core components, creating a dependency graph that determines development order and integration points.

**Key Principle**: CLI commands are thin wrappers around core components, not independent implementations.

## Core Architecture Mapping

### Phase 0: Bootstrap Commands → Core Infrastructure

#### `weft init`
**Purpose**: Initialize Weft project in current directory
**Dependencies**:
- `weft.context.initialize_project` ✅ **CRITICAL PATH**
- `weft.context.needs_initialization`

**Implementation Location**: `weft/commands.py:init()`
```python
@cli.command()
@click.option('--force', is_flag=True, help='Reinitialize existing project')
def init(force):
    """Initialize a weft project in the current directory."""
    from weft.context import initialize_project, WeftContextError
    
    try:
        root_path = initialize_project(force=force)
        click.echo(f"✅ Initialized weft project in {root_path / '.weft'}")
    except WeftContextError as e:
        click.echo(f"Error: {e}", err=True)
        ctx.exit(1)
```

**Development Order**: Must be implemented early for project setup

#### `weft bootstrap` 
**Purpose**: Initialize Weft system with primordial worker
**Dependencies**: 
- `weft.core.manager.Manager` ✅ **CRITICAL PATH**
- `weft.core.taskspec.TaskSpec` ✅ **EXISTS**
- `weft.context.get_context` ✅ **CRITICAL PATH**
- SimpleBroker Queue (external)

**Implementation Location**: `weft/commands.py:bootstrap()`
```python
@cli.command()
@click.option('--config', type=click.Path(exists=True), help='Config file')
@click.option('--recover', is_flag=True, help='Recovery mode')
@click.pass_context
def bootstrap(ctx, config, recover):
    """Start the primordial worker."""
    from weft.context import get_context
    from weft.core.manager import Manager
    
    context = get_context()  # Auto-discovery
    worker_spec = create_primordial_worker_spec(config, context)
    worker = Manager(worker_spec, context=context)
    worker.run()  # Blocks until shutdown
```

**Development Order**: Must be first - everything else requires bootstrap

---

### Phase 1: Task Execution Commands → Core Task Engine

#### `weft run` 
**Purpose**: Execute tasks (primary user interface)
**Dependencies**:
- `weft.core.manager.Client` ✅ **CRITICAL PATH**
- `weft.core.tasks.Task` ✅ **CRITICAL PATH**
- `weft.core.executor.FunctionExecutor` / `CommandExecutor`
- `weft.integration.templates.TaskTemplate`

**Implementation Location**: `weft/commands.py:run()`
```python
@cli.command()
@click.argument('command_args', nargs=-1)
@click.option('--spec', type=click.File('r'), help='TaskSpec JSON file')
@click.option('--timeout', type=int, help='Timeout in seconds')
@click.option('--wait', is_flag=True, help='Wait for completion')
@click.pass_context
def run(ctx, command_args, spec, timeout, wait):
    """Execute a task."""
    context = ctx.obj['context']
    manager = Client(context)
    
    if spec:
        taskspec = TaskSpec.model_validate(json.load(spec))
    else:
        taskspec = create_taskspec_from_args(command_args, timeout)
    
    tid = manager.submit(taskspec)
    click.echo(tid)
    
    if wait:
        result = manager.wait_for_completion(tid)
        click.echo(result)
```

#### `weft status`
**Purpose**: Check task status and system state
**Dependencies**:
- `weft.tools.observability.TaskMonitor` ✅ **CRITICAL PATH**
- `weft.core.manager.Client`
- `weft.tools.tid_tools.TIDResolver`

**Implementation Location**: `weft/commands.py:status()`
```python
@cli.command()
@click.argument('tid', required=False)
@click.option('--all', is_flag=True, help='Show all tasks')
@click.option('--json', 'json_output', is_flag=True, help='JSON output')
@click.option('--watch', is_flag=True, help='Continuous monitoring')
@click.pass_context
def status(ctx, tid, all, json_output, watch):
    """Check task status."""
    context = ctx.obj['context']
    monitor = TaskMonitor(context)
    
    if tid:
        # Resolve short TID if needed
        resolver = TIDResolver(context)
        full_tid = resolver.resolve_short_tid(tid) if len(tid) < 19 else tid
        task_status = monitor.get_task_status(full_tid)
    else:
        task_status = monitor.get_all_tasks() if all else monitor.get_active_tasks()
    
    if json_output:
        click.echo(json.dumps(task_status))
    else:
        click.echo(format_status_table(task_status))
```

#### `weft result`
**Purpose**: Retrieve task output
**Dependencies**:
- `weft.core.manager.Client`
- `weft.tools.tid_tools.TIDResolver`
- SimpleBroker Queue operations

**Implementation Location**: `weft/commands.py:result()`

---

### Phase 2: Process Management Commands → OS Integration Tools

#### `weft ps`
**Purpose**: List weft processes via OS tools
**Dependencies**:
- `weft.tools.process_tools.ProcessManager` ✅ **CRITICAL PATH**
- `weft.tools.tid_tools.TIDResolver`

**Implementation Location**: `weft/commands.py:ps()`
```python
@cli.command()
@click.option('--failed', is_flag=True, help='Show only failed tasks')
@click.option('--pattern', help='Filter by pattern')
@click.option('--json', 'json_output', is_flag=True, help='JSON output')
def ps(failed, pattern, json_output):
    """List weft processes."""
    process_mgr = ProcessManager()
    processes = process_mgr.find_weft_processes(pattern)
    
    if failed:
        processes = [p for p in processes if 'failed' in p.get('status', '')]
    
    if json_output:
        click.echo(json.dumps(processes))
    else:
        click.echo(format_process_table(processes))
```

#### `weft kill` / `weft stop`
**Purpose**: Terminate tasks gracefully or forcefully
**Dependencies**:
- `weft.tools.process_tools.ProcessManager`
- `weft.core.manager.Client` (for graceful stop)
- `weft.tools.tid_tools.TIDResolver`

Pattern-based invocations delegate to `queue broadcast --pattern 'T*.ctrl_in'`, so the CLI simply emits a control payload to all matching tasks via SimpleBroker’s selective broadcast.

#### `weft top`
**Purpose**: Live task monitoring (like Unix top)
**Dependencies**:
- `weft.tools.process_tools.ProcessManager`
- `weft.tools.observability.TaskMonitor`
- `weft.core.resource_monitor.ResourceMonitor` (data source)

---

### Phase 3: Worker Management Commands → Recursive Architecture

#### `weft worker`
**Purpose**: Manage worker tasks
**Dependencies**:
- `weft.core.manager.Manager` ✅ **CRITICAL PATH**
- `weft.core.manager.Client`
- `weft.integration.templates.TaskRegistry`

**Implementation Location**: `weft/commands.py:worker_group()`
```python
@cli.group()
def worker():
    """Manage worker tasks."""
    pass

@worker.command()
@click.option('--type', 'worker_type', default='general', help='Worker type')
@click.option('--registry', type=click.Path(exists=True), help='Task registry file')
@click.pass_context
def start(ctx, worker_type, registry):
    """Start a worker."""
    context = ctx.obj['context']
    registry_obj = TaskRegistry.load(registry) if registry else TaskRegistry.default()
    tid = WorkerLifecycle.spawn_worker(worker_type, registry_obj)
    click.echo(f"Started worker {tid}")

@worker.command()
@click.argument('tid')
@click.option('--force', is_flag=True, help='Force kill')
@click.pass_context
def stop(ctx, tid, force):
    """Stop a worker."""
    WorkerLifecycle.stop_worker(tid, graceful=not force)
```

---

### Phase 4: Queue Operations Commands → SimpleBroker Integration

#### `weft queue`
**Purpose**: Direct queue manipulation with selective broadcast and alias management (delegates to SimpleBroker)
**Dependencies**:
- SimpleBroker Queue API (external)
- `weft.core.context.WeftContext`

**Implementation Location**: `weft/cli.py:queue_app`, `weft/commands/queue.py`

**Key Features**:
- **Basic Operations**: read, write, peek, move, list, watch, delete
- **Selective Broadcast**: `broadcast MESSAGE --pattern GLOB` for fanout messaging
- **Alias Management**: `alias add/list/remove` for queue name shortcuts

**Example Implementation**:
```python
@queue_app.command("broadcast")
def queue_broadcast(
    message: str | None = None,
    pattern: str | None = typer.Option(None, "--pattern", "-p",
                                     help="fnmatch-style pattern to limit target queues"),
) -> None:
    _emit_queue_result(queue_cmd.broadcast_command(message, pattern=pattern))

@alias_app.command("add")
def alias_add(alias: str, target: str, quiet: bool = False) -> None:
    _emit_queue_result(queue_cmd.alias_add_command(alias, target, quiet=quiet))
```

---

### Phase 5: Advanced Features Commands → Integration Layer

#### `weft pipe` / `weft pipeline`
**Purpose**: Task pipeline management
**Dependencies**:
- `weft.integration.pipelines.PipelineBuilder` ✅ **CRITICAL PATH**
- `weft.core.manager.Client`
- `weft.integration.templates.TaskTemplate`

#### `weft template`
**Purpose**: Task template management
**Dependencies**:
- `weft.integration.templates.TaskTemplate` ✅ **CRITICAL PATH**
- `weft.core.context.WeftContext`

#### `weft batch`
**Purpose**: Submit multiple tasks
**Dependencies**:
- `weft.core.manager.Client`
- `weft.integration.templates.TaskTemplate`

---

## Development Dependency Graph

### Critical Path Components (Must Build First)
1. **`weft.core.taskspec.TaskSpec`** ✅ **EXISTS**
2. **`weft.context`** ✅ **CRITICAL PATH** - Git-like project discovery and initialization
3. **`weft.core.manager.Manager`**
4. **`weft.core.tasks.Task`**
5. **`weft.core.manager.Client`**

### Secondary Components (Depend on Critical Path)
6. **`weft.tools.process_tools.ProcessManager`**
7. **`weft.tools.observability.TaskMonitor`**
8. **`weft.tools.tid_tools.TIDResolver`**

### Integration Components (Build After Core Stable)
9. **`weft.integration.templates.TaskTemplate`**
10. **`weft.integration.pipelines.PipelineBuilder`**

## CLI Development Order

### Phase 0: Bootstrap (Week 1)
**Commands**: `weft bootstrap`
**Required Components**:
- `Manager` basic implementation
- `WeftContext` directory scoping
- CLI framework setup

**CLI Implementation**:
```python
# weft/cli.py - Framework setup
@click.group()
@click.option('-d', '--dir', 'context_dir')
@click.option('-f', '--file', 'db_file', default='.broker.db')
@click.pass_context
def cli(ctx, context_dir, db_file):
    ctx.ensure_object(dict)
    ctx.obj['context'] = WeftContext(context_dir)

# weft/commands.py - Bootstrap command
@cli.command()
def bootstrap():
    # Implementation
```

### Phase 1: Core Task Management (Week 2)
**Commands**: `weft run`, `weft status`, `weft result`
**Required Components**:
- `Client` for submission/querying
- `Task` execution engine
- `TaskMonitor` for status

### Phase 2: Process Management (Week 2-3)
**Commands**: `weft ps`, `weft kill`, `weft stop`, `weft top`
**Required Components**:
- `ProcessManager` for OS integration
- `TIDResolver` for TID mapping

### Phase 3: Worker Management (Week 3)
**Commands**: `weft worker start/stop/list`
**Required Components**:
- Enhanced `Manager` with lifecycle
- Worker registry system

### Phase 4: Queue Operations (Week 3-4)
**Commands**: `weft queue read/write/peek/move`
**Required Components**:
- Direct SimpleBroker integration
- Queue validation and safety

### Phase 5: Advanced Features (Week 4+)
**Commands**: `weft pipe`, `weft template`, `weft batch`
**Required Components**:
- Pipeline builder
- Template management
- Batch processing

## Integration Test Strategy

### CLI-Component Integration Tests
Each CLI command must have integration tests that verify:

1. **Component Interaction**:
```python
def test_run_command_integration():
    """Test that 'weft run' properly uses Client."""
    result = runner.invoke(cli, ['run', 'echo', 'hello'])
    assert result.exit_code == 0
    
    # Verify Client was called correctly
    tasks = TaskMonitor().get_all_tasks()
    assert len(tasks) == 1
    assert tasks[0]['name'].startswith('echo')
```

2. **Error Code Mapping**:
```python
def test_status_command_not_found():
    """Test that 'weft status nonexistent' returns exit code 2."""
    result = runner.invoke(cli, ['status', 'T999999999999999999'])
    assert result.exit_code == 2
    assert "not found" in result.output
```

3. **JSON Output Consistency**:
```python
def test_all_commands_json_output():
    """Verify all commands support --json and produce valid JSON."""
    commands_with_json = ['status', 'ps', 'result', 'list']
    for cmd in commands_with_json:
        result = runner.invoke(cli, [cmd, '--json'])
        assert result.exit_code in [0, 2]  # Success or not found
        if result.output.strip():
            json.loads(result.output)  # Should not raise
```

## CLI Command Reference Map

| Command | Module | Component | Exit Codes | JSON Support |
|---------|--------|-----------|------------|--------------|
| `init` | `commands.py` | `weft.context` | 0, 1 | ❌ |
| `bootstrap` | `commands.py` | `Manager` | 0, 1 | ❌ |
| `run` | `commands.py` | `Client` | 0, 1, 2 | ❌ |
| `status` | `commands.py` | `TaskMonitor` | 0, 2 | ✅ |
| `result` | `commands.py` | `Client` | 0, 2, 124 | ✅ |
| `list` | `commands.py` | `TaskMonitor` | 0 | ✅ |
| `ps` | `commands.py` | `ProcessManager` | 0 | ✅ |
| `kill` | `commands.py` | `ProcessManager` | 0, 2 | ❌ |
| `stop` | `commands.py` | `Client` | 0, 2 | ❌ |
| `top` | `commands.py` | `ProcessManager`, `TaskMonitor` | 0, 130 | ❌ |
| `worker start` | `commands.py` | `Manager` | 0, 1 | ❌ |
| `worker stop` | `commands.py` | `Manager` | 0, 2 | ❌ |
| `worker list` | `commands.py` | `TaskMonitor` | 0 | ✅ |
| `queue read` | `commands.py` | SimpleBroker | 0, 2 | ✅ |
| `queue write` | `commands.py` | SimpleBroker | 0, 1 | ✅ |
| `queue peek` | `commands.py` | SimpleBroker | 0, 2 | ✅ |
| `queue move` | `commands.py` | SimpleBroker | 0, 1, 2 | ✅ |
| `queue list` | `commands.py` | SimpleBroker | 0 | ✅ |
| `queue watch` | `commands.py` | SimpleBroker | 0, 130 | ✅ |
| `queue delete` | `commands.py` | SimpleBroker | 0, 1, 2 | ✅ |
| `queue broadcast` | `commands.py` | SimpleBroker | 0, 2 | ✅ |
| `queue alias add` | `commands.py` | SimpleBroker | 0, 1 | ✅ |
| `queue alias list` | `commands.py` | SimpleBroker | 0 | ✅ |
| `queue alias remove` | `commands.py` | SimpleBroker | 0, 1, 2 | ✅ |
| `tid` | `commands.py` | `TIDResolver` | 0, 2 | ✅ |
| `template create` | `commands.py` | `TaskTemplate` | 0, 1 | ❌ |
| `template list` | `commands.py` | `TaskTemplate` | 0 | ✅ |
| `pipe` | `commands.py` | `PipelineBuilder` | 0, 1, 2 | ❌ |
| `batch` | `commands.py` | `Client` | 0, 1 | ✅ |

## Error Handling Crosswalk

### Component Exception → CLI Exit Code Mapping

```python
# weft/cli.py - Global error handling
def handle_weft_exceptions(func):
    """Decorator to map component exceptions to CLI exit codes."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except TaskNotFound as e:
            click.echo(f"Task not found: {e}", err=True)
            sys.exit(2)
        except TaskTimeout as e:
            click.echo(f"Task timed out: {e}", err=True)
            sys.exit(124)
        except QueueEmpty as e:
            click.echo(f"Queue empty: {e}", err=True)
            sys.exit(2)
        except WeftError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        except KeyboardInterrupt:
            click.echo("Interrupted", err=True)
            sys.exit(130)
    return wrapper

# Apply to all CLI commands
@cli.command()
@handle_weft_exceptions
def status(tid):
    # Implementation can focus on logic, not error handling
    monitor = TaskMonitor()
    return monitor.get_task_status(tid)  # Raises TaskNotFound if needed
```

## Testing Integration Points

### CLI-Component Test Matrix

| Component | CLI Commands | Test Requirements |
|-----------|--------------|-------------------|
| `TaskSpec` | `run` | Valid/invalid spec handling |
| `Task` | `run`, `status` | Execution state tracking |
| `Client` | `run`, `status`, `result` | Submission and querying |
| `Manager` | `bootstrap`, `worker` | Lifecycle management |
| `ProcessManager` | `ps`, `kill`, `top` | OS integration accuracy |
| `TaskMonitor` | `status`, `list` | State aggregation |
| `TIDResolver` | `status`, `tid` | TID mapping consistency |

### Continuous Integration Pipeline

```yaml
# .github/workflows/cli-integration.yml
name: CLI-Component Integration Tests
on: [push, pull_request]

jobs:
  cli-integration:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install -e .[dev]
          pip install simplebroker
      - name: Run CLI integration tests
        run: |
          pytest tests/test_cli_integration/ -v
          # Test actual CLI commands work
          weft bootstrap --help
          weft run --help
          # Test that all commands have proper exit codes
          python scripts/test_exit_codes.py
```

## Related Documents

- **[10-CLI_Interface.md](10-CLI_Interface.md)** - Complete CLI command specifications
- **[09-Implementation_Plan.md](09-Implementation_Plan.md)** - Development phases and component locations  
- **[01-Core_Components.md](01-Core_Components.md)** - Component architecture and module organization
- **[08-Testing_Strategy.md](08-Testing_Strategy.md)** - Testing approach and standards

---

This crosswalk ensures that CLI development happens in lockstep with component development, preventing integration issues and ensuring the CLI is a proper interface to the underlying system rather than a parallel implementation.
