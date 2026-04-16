# Good-to-Excellent: Specification, UX, and Engineering Polish

Status: roadmap
Source specs: see Source Documents below
Superseded by: none

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Address the gaps identified in a full-project review that prevent weft from being *excellent* — unclear spec boundaries, discovery friction for advanced features, and sparse inline documentation in complex code paths.

**Architecture:** Documentation and spec improvements organized in three tiers: (1) quick code/spec fixes that remove ambiguity, (2) spec clarifications that define implicit boundaries, (3) new documentation that improves developer discovery. No behavioral changes to weft itself.

**Tech Stack:** Markdown (specs), Python docstrings/comments, Typer CLI help text.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `weft/core/resource_monitor.py` | Fix mypy psutil import redefinition |
| Modify | `weft/helpers.py` | Fix mypy bool return in `pid_is_live` |
| Modify | `weft/commands/run.py` | Expand `cmd_run` docstring and help epilog |
| Modify | `weft/core/manager.py` | Add inline algorithm docs to leadership election |
| Modify | `weft/core/tasks/consumer.py` | Add inline docs to deferred control handling |
| Modify | `weft/helpers.py` | Add algorithm overview to process tree termination |
| Modify | `docs/specifications/02-TaskSpec.md` | Clarify state vs metadata boundary; reserved policy timing |
| Modify | `docs/specifications/05-Message_Flow_and_State.md` | Add large-output reference format subsection |
| Modify | `docs/specifications/13-Agent_Runtime.md` | Clarify per_task subprocess requirement |
| Modify | `README.md` | Add links to agent runtime and pipeline specs; backend decision matrix |
| Create | `docs/tutorials/first-task.md` | Worked end-to-end tutorial for newcomers |

---

### Task 1: Fix mypy type-checking issues

Two trivial fixes that enable clean CI.

**Files:**
- Modify: `weft/core/resource_monitor.py:21-27`
- Modify: `weft/helpers.py:247`

- [ ] **Step 1: Fix psutil import redefinition in resource_monitor.py**

In `weft/core/resource_monitor.py`, the variable `_psutil_module` is declared then immediately redefined by the import, triggering mypy `no-redef`. Fix by renaming the import alias:

```python
# Before (lines 21-27):
_psutil_module: Any | None
try:  # pragma: no cover - psutil optional at import time
    import psutil as _psutil_module
except Exception:  # pragma: no cover
    _psutil_module = None

psutil: Any | None = _psutil_module

# After:
_psutil_imported: Any | None
try:  # pragma: no cover - psutil optional at import time
    import psutil as _psutil_imported
except Exception:  # pragma: no cover
    _psutil_imported = None

psutil: Any | None = _psutil_imported
```

- [ ] **Step 2: Fix bool return in helpers.py pid_is_live**

In `weft/helpers.py:247`, `process.status()` returns `Any`, so the comparison returns `Any`. Wrap in `bool()`:

```python
# Before (line 247):
        return process.status() != psutil.STATUS_ZOMBIE

# After:
        return bool(process.status() != psutil.STATUS_ZOMBIE)
```

- [ ] **Step 3: Run mypy to verify fixes**

Run: `./.venv/bin/python -m mypy weft/core/resource_monitor.py weft/helpers.py`
Expected: No errors on these files.

- [ ] **Step 4: Run full test suite to verify no regressions**

Run: `./.venv/bin/python -m pytest tests/ -x -q`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add weft/core/resource_monitor.py weft/helpers.py
git commit -m "$(cat <<'EOF'
Fix two minor mypy type errors

Rename psutil import alias to avoid redefinition warning in
resource_monitor.py. Wrap status comparison in bool() in
helpers.py pid_is_live.
EOF
)"
```

---

### Task 2: Expand `weft run` CLI help text

The main entry point has 22 parameters and a one-sentence docstring. This is the most important `--help` output in the project.

**Files:**
- Modify: `weft/commands/run.py` (docstring of `cmd_run` function)

- [ ] **Step 1: Find the cmd_run function signature**

Run: `grep -n "def cmd_run" weft/commands/run.py`
Read the function to find the current docstring.

- [ ] **Step 2: Expand the docstring**

Replace the existing docstring with a comprehensive one that covers the four execution modes and common patterns. The docstring is rendered by Typer as `--help` output:

```python
def cmd_run(
    # ... existing params ...
) -> None:
    """Execute a command, function, task spec, or pipeline.

    Four execution modes (mutually exclusive):

    \b
      weft run COMMAND [ARGS...]        Run a shell command
      weft run --function mod:fn        Call a Python function
      weft run --spec task.json         Run a TaskSpec file or stored spec
      weft run --pipeline pipe.json     Run a pipeline spec

    \b
    Common patterns:
      weft run echo "hello"                     Simple command
      weft run --timeout 60 --memory 512 heavy  With resource limits
      weft run --no-wait long-task.sh            Fire and forget
      printf "data" | weft run -- processor     Pipe stdin
      weft run --function mymod:fn --arg x      Function with args

    By default, waits for the task to complete and prints output.
    Use --no-wait to submit and return immediately (prints TID).
    """
```

Note: The `\b` markers tell Typer/Click to preserve the formatting of the following paragraph literally rather than reflowing it.

- [ ] **Step 3: Verify help output**

Run: `./.venv/bin/python -m weft run --help`
Expected: The help text now shows the four modes and common patterns clearly.

- [ ] **Step 4: Commit**

```bash
git add weft/commands/run.py
git commit -m "$(cat <<'EOF'
Expand weft run help text with modes and examples

The main entry point had a one-sentence docstring. Now shows
the four execution modes and common usage patterns.
EOF
)"
```

---

### Task 3: Add inline algorithm documentation to complex code

Three areas where the code is correct but the *strategy* isn't explained.

**Files:**
- Modify: `weft/core/manager.py:454-483`
- Modify: `weft/core/tasks/consumer.py:234-290`
- Modify: `weft/helpers.py` (process tree termination, find the `_terminate_process_tree` or similar function near line 268+)

- [ ] **Step 1: Document leadership election in manager.py**

Read `weft/core/manager.py` around lines 448-483 to understand the full algorithm. Add a block comment above `_maybe_yield_leadership` explaining the strategy:

```python
    def _maybe_yield_leadership(self, *, force: bool = False) -> bool:
        """Yield leadership to a lower-TID manager if one exists.

        Leadership election algorithm:
        - The manager with the lowest TID is the leader (deterministic,
          no coordination needed — all managers read the same registry).
        - Checks are throttled to _leader_check_interval_ns to avoid
          registry contention.
        - If this manager has active children, it begins a graceful drain
          (finish current work, then yield) rather than stopping abruptly.
        - Persistent children prevent yielding entirely — the operator
          must stop them explicitly before leadership can transfer.
        - A manager with no children yields immediately by marking itself
          cancelled and unregistering.
        """
```

- [ ] **Step 2: Document deferred control handling in consumer.py**

Read `weft/core/tasks/consumer.py` around lines 234-290 to understand the pattern. Add a block comment above `_defer_active_control`:

```python
    def _defer_active_control(self, command: str, timestamp: int) -> None:
        """Defer active STOP/KILL finalization back to the main task thread.

        Thread-safety pattern: The background control poller detects STOP/KILL
        promptly, but must not publish terminal state or apply reserved-queue
        policy while the main thread is mid-execution. Instead it:

        1. Records the command and timestamp here (atomic flag set).
        2. Sets should_stop = True so the main loop exits after the
           current work item.
        3. The main thread calls _finalize_deferred_active_control() after
           the work item completes, which applies the reserved policy and
           publishes terminal state on the correct thread.

        This avoids races between "task completed normally" and "task was
        stopped" — only the main thread publishes terminal state.
        """
```

- [ ] **Step 3: Document process tree termination in helpers.py**

Read `weft/helpers.py` near line 268+ to find the process tree termination function. Add an algorithm overview comment. The exact content depends on the function found, but it should explain:
- Why tree termination (not just the root process) is needed
- The order of operations (children first or parent first?)
- What happens when processes don't respond to SIGTERM

- [ ] **Step 4: Run tests to verify no regressions**

Run: `./.venv/bin/python -m pytest tests/ -x -q`
Expected: All tests pass (comments only, no behavior change).

- [ ] **Step 5: Commit**

```bash
git add weft/core/manager.py weft/core/tasks/consumer.py weft/helpers.py
git commit -m "$(cat <<'EOF'
Add algorithm docs to leadership election, deferred control, process termination

These three code paths are correct but non-obvious. Added block
comments explaining the strategy and thread-safety invariants.
EOF
)"
```

---

### Task 4: Clarify spec ambiguities in 02-TaskSpec.md

Two clarifications: (a) the `state` vs `metadata` semantic boundary, and (b) reserved policy timing including timeouts.

**Files:**
- Modify: `docs/specifications/02-TaskSpec.md`

- [ ] **Step 1: Read the current state/metadata sections**

Read `docs/specifications/02-TaskSpec.md` and find the `state` and `metadata` schema sections (around lines 112-134 in the JSON schema). Also find any prose explaining them.

- [ ] **Step 2: Add state vs metadata boundary definition**

After the JSON schema block (after the closing ` ``` ` of the schema), find the section that discusses `state` and `metadata`. Add a new subsection:

```markdown
### State vs Metadata Semantic Boundary [TS-1.4]

The `state` and `metadata` sections are both mutable at runtime, but they serve
different owners:

- **`state`** is system-owned. Weft's task lifecycle (BaseTask, Consumer,
  Manager) writes `status`, `pid`, `started_at`, resource metrics, and
  `return_code`. External code should treat `state` as read-only — it
  reflects what the system observes.

- **`metadata`** is caller-owned. The task creator sets tags, owner, session
  IDs, and any application-specific key-value pairs. Tasks and external
  callers can update metadata via `update_metadata` control messages.
  Weft does not interpret metadata contents.

Rule of thumb: if Weft writes it automatically, it belongs in `state`. If
the user or application writes it, it belongs in `metadata`.

_Implementation mapping_: `StateSection` fields are updated by
`BaseTask._report_state_change()` and `Consumer._finalize_result()`.
`metadata` is updated by `BaseTask._handle_update_metadata()` from
`ctrl_in` messages and by `TaskSpec.update_metadata()` at creation time.
```

- [ ] **Step 3: Clarify reserved policy timing to include timeouts**

Find the `reserved_policy_on_stop` and `reserved_policy_on_error` field descriptions in the JSON schema comments (around lines 91-92). Update the `reserved_policy_on_error` comment:

```jsonc
    "reserved_policy_on_error": "keep",     // OPTIONAL. Behaviour for messages left in T{tid}.reserved when execution fails, times out, or is killed. Same options and default as above.
```

Also find any prose section discussing reserved policies (search for `[TS-1.1]` or "reserved policy") and add a clarifying sentence:

```markdown
Timeouts are treated as error exits for reserved-policy purposes:
`reserved_policy_on_error` applies when a task exceeds its timeout, is
killed, or fails with a non-zero exit code.
```

- [ ] **Step 4: Commit**

```bash
git add docs/specifications/02-TaskSpec.md
git commit -m "$(cat <<'EOF'
Clarify state vs metadata boundary and reserved policy timing

Define [TS-1.4]: state is system-owned, metadata is caller-owned.
Clarify that reserved_policy_on_error covers timeouts and kills,
not just non-zero exits.
EOF
)"
```

---

### Task 5: Add large-output reference format to spec 05

The spill mechanism exists in code but the reference format is unspecified.

**Files:**
- Modify: `docs/specifications/05-Message_Flow_and_State.md`
- Read (for reference): `weft/core/tasks/base.py` (find `_spill_large_output`)

- [ ] **Step 1: Find the spill implementation**

Run: `grep -n "spill_large_output\|_spill\|large.output\|output_size_limit" weft/core/tasks/base.py`

Read the relevant function to understand the reference format (file path, metadata fields, encoding).

- [ ] **Step 2: Find the existing large-output mention in spec 05**

Run: `grep -n "large.output\|spill\|output_size_limit" docs/specifications/05-Message_Flow_and_State.md`

Read the surrounding context to find where to insert the new subsection.

- [ ] **Step 3: Add large-output reference format subsection**

After the existing mention of large-output references in spec 05, add:

```markdown
### Large-Output Reference Format [MF-2.1]

When a result payload exceeds `spec.output_size_limit_mb` (default 10 MB),
the consumer spills the payload to a file and writes a reference envelope
to the outbox instead of the raw data.

Reference envelope structure:

```jsonc
{
  "type": "large_output_ref",
  "path": "/absolute/path/to/.weft/spill/T{tid}-output.dat",
  "size_bytes": 15728640,
  "encoding": "utf-8" | "binary",
  "created_at": 1837025672140161024   // nanosecond timestamp
}
```

Rules:
- The `path` is an absolute filesystem path. It is only valid on the host
  where the task executed.
- `weft result` does not currently auto-dereference large-output references.
  Callers must read the file at `path` directly.
- Spill files are cleaned up by `weft system tidy` but not by task cleanup
  (they must survive until the caller reads them).

_Implementation mapping_: `weft/core/tasks/base.py` `_spill_large_output()`.
```

**Important:** The exact field names and structure above are templates. Read the actual `_spill_large_output` implementation first and adjust the spec to match what the code actually produces. If the code produces different fields, use those fields. If the code doesn't produce a structured envelope yet, document what it does produce and note the gap.

- [ ] **Step 4: Commit**

```bash
git add docs/specifications/05-Message_Flow_and_State.md
git commit -m "$(cat <<'EOF'
Spec the large-output reference format [MF-2.1]

Documents the envelope structure produced by _spill_large_output
so operators and tooling authors know how to dereference spilled results.
EOF
)"
```

---

### Task 6: Clarify agent runtime subprocess requirement

**Files:**
- Modify: `docs/specifications/13-Agent_Runtime.md`

- [ ] **Step 1: Find the per_task scope description**

Run: `grep -n "per_task\|conversation_scope\|may run" docs/specifications/13-Agent_Runtime.md`

Read the surrounding context, especially section [AR-6].

- [ ] **Step 2: Read the implementation to determine actual behavior**

Run: `grep -rn "per_task\|conversation_scope\|AgentSession" weft/core/ --include="*.py"`

Determine whether `per_task` scope always creates a subprocess, or whether it's optional.

- [ ] **Step 3: Update the spec to match reality**

If `per_task` always creates a subprocess, change "may run the live model conversation in a dedicated subprocess" to "runs the live model conversation in a dedicated subprocess."

If it's genuinely optional, add a clarifying note: "The subprocess is created when [specific condition]; otherwise the conversation runs in the task process."

- [ ] **Step 4: Commit**

```bash
git add docs/specifications/13-Agent_Runtime.md
git commit -m "$(cat <<'EOF'
Clarify per_task conversation scope subprocess behavior [AR-6]

Remove ambiguous 'may' — specify whether per_task scope always
creates a subprocess or under what conditions it does.
EOF
)"
```

---

### Task 7: Improve README discovery for advanced features

The README covers the common path well but hides agent runtime, pipelines, and backend selection.

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Read the current README structure**

Read `README.md` in full to understand the section ordering and where to insert new content.

- [ ] **Step 2: Add a "Going Deeper" navigation section**

After the Quick Start and Core Concepts sections, before the CLI Reference, add:

```markdown
## Going Deeper

| Topic | Where to look |
|-------|--------------|
| Agent tasks (LLM-powered) | [Agent Runtime spec](docs/specifications/13-Agent_Runtime.md) |
| Pipeline composition | [Pipeline spec](docs/specifications/12-Pipeline_Composition_and_UX.md) |
| TaskSpec schema reference | [TaskSpec spec](docs/specifications/02-TaskSpec.md) |
| Queue patterns and state | [Message Flow spec](docs/specifications/05-Message_Flow_and_State.md) |
| System invariants | [Invariants spec](docs/specifications/07-System_Invariants.md) |
| Runner plugins (Docker, sandbox) | [Core Components spec](docs/specifications/01-Core_Components.md#runner-plugin-boundary-cc-31) |
```

- [ ] **Step 3: Add a backend selection note**

Find the Installation section. After the install commands and the existing prose about backend selection, add:

```markdown
### Choosing a Backend

| | SQLite (default) | Postgres |
|---|---|---|
| Setup | Zero config | Requires running Postgres |
| Concurrency | Single-writer (fine for most workloads) | Full MVCC |
| Persistence | File on disk | Database server |
| Best for | Local dev, single-machine agents, CI | Multi-host, high-concurrency production |

SQLite is the right default for most users. Switch to Postgres when you need
multi-host task submission or high write concurrency.
```

- [ ] **Step 4: Verify the links work**

Run: `ls docs/specifications/13-Agent_Runtime.md docs/specifications/12-Pipeline_Composition_and_UX.md`
Expected: Both files exist (if 12 doesn't exist, check the actual filename — it may be `12-Future_Ideas.md` or similar; adjust the link).

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
Add 'Going Deeper' nav and backend decision matrix to README

Surfaces agent runtime, pipeline, and other spec links that were
previously hard to discover. Adds SQLite vs Postgres comparison table.
EOF
)"
```

---

### Task 8: Write a first-task tutorial

A worked example that takes a newcomer from zero to running a task with resource limits and result collection.

**Files:**
- Create: `docs/tutorials/first-task.md`
- Modify: `README.md` (add link to tutorial)

- [ ] **Step 1: Create the tutorials directory**

Run: `mkdir -p docs/tutorials`

- [ ] **Step 2: Write the tutorial**

Create `docs/tutorials/first-task.md`:

```markdown
# Your First Weft Task

This tutorial walks through creating and running a task from scratch.
By the end you'll understand: project init, inline commands, task specs,
resource limits, and result collection.

## Prerequisites

```bash
uv add weft
```

## 1. Initialize a project

```bash
mkdir my-project && cd my-project
weft init
```

This creates a `.weft/` directory for task specs, pipelines, and project config.

## 2. Run an inline command

```bash
$ weft run echo "hello from weft"
hello from weft
```

Under the hood: weft started a manager (if one wasn't running), submitted a
spawn request, the manager created a Consumer process, the consumer ran
`echo`, wrote the output to an outbox queue, and weft printed it.

## 3. Run with resource limits

```bash
$ weft run --timeout 10 --memory 256 python -c "print('constrained')"
constrained
```

If the command exceeds 256 MB memory or 10 seconds, weft terminates it and
reports a `timeout` or `killed` status.

## 4. Fire and forget

```bash
$ weft run --no-wait sleep 5
1837025672140161024

$ weft status
System: OK
Tasks: 1 running

$ weft result 1837025672140161024
# (empty — sleep produces no output)
```

`--no-wait` returns the task ID (TID) immediately. Use `weft result <TID>`
to collect output later.

## 5. Run a Python function

```bash
# Create a simple module
cat > processor.py << 'PYEOF'
def greet(name: str) -> str:
    return f"Hello, {name}!"
PYEOF

$ weft run --function processor:greet --arg "world"
Hello, world!
```

## 6. Create a reusable task spec

```bash
$ weft spec create my-greeter \
    --type function \
    --target processor:greet

$ weft run --spec my-greeter --arg "weft"
Hello, weft!

$ weft spec show my-greeter
# Shows the full TaskSpec JSON
```

Task specs are stored in `.weft/tasks/` and can be version-controlled.

## 7. Check task history

```bash
$ weft task list
TID                  NAME        STATUS     TIME
1837025672140161024  sleep       completed  5.0s
1837025672140161025  greet       completed  0.1s
...

$ weft task status 1837025672140161025
# Shows full task details including resource usage
```

## Next steps

- [TaskSpec schema reference](../specifications/02-TaskSpec.md) — all fields and options
- [Agent tasks](../specifications/13-Agent_Runtime.md) — LLM-powered tasks
- [Pipeline composition](../specifications/12-Pipeline_Composition_and_UX.md) — chaining tasks
- [CLI reference](../../README.md#cli-reference) — all commands
```

- [ ] **Step 3: Link the tutorial from README**

In the README, after the Quick Start section, add a line:

```markdown
For a guided walkthrough, see [Your First Weft Task](docs/tutorials/first-task.md).
```

- [ ] **Step 4: Verify the tutorial's links resolve**

Run: `ls docs/specifications/02-TaskSpec.md docs/specifications/13-Agent_Runtime.md`
Expected: Both exist. Also check the pipeline spec filename (may need adjustment).

- [ ] **Step 5: Commit**

```bash
git add docs/tutorials/first-task.md README.md
git commit -m "$(cat <<'EOF'
Add first-task tutorial for newcomers

Worked example covering init, inline commands, resource limits,
fire-and-forget, Python functions, and reusable task specs.
EOF
)"
```

---

## Self-Review Checklist

**Spec coverage:** The review identified 9 high/medium issues. This plan addresses all 9:
1. mypy fixes (Task 1) -- done
2. `weft run` help text (Task 2) -- done
3. Algorithm inline docs (Task 3) -- done
4. state vs metadata boundary (Task 4) -- done
5. Reserved policy timing (Task 4) -- done
6. Large-output reference format (Task 5) -- done
7. Agent per_task subprocess clarity (Task 6) -- done
8. README discovery / backend matrix (Task 7) -- done
9. First-task tutorial (Task 8) -- done

**Placeholder scan:** No TBD/TODO/placeholder patterns. Every step has concrete content or explicit "read the code and adjust" instructions where the exact content depends on implementation details.

**Type consistency:** N/A — no new code interfaces introduced. All changes are docstrings, comments, specs, and docs.

**Not in scope (intentionally deferred):**
- Runner plugin protocol spec — needs design discussion, not just documentation
- Pipeline spec consolidation — would restructure spec numbering, high churn for moderate gain
- Spec reference validation tooling — tooling task, not documentation
- Docstring coverage for ~370 internal helpers — incremental ongoing work, not a plan task
