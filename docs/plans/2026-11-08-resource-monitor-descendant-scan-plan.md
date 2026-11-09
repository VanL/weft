# Resource Monitor Descendant Scan and Quiet Unlimited Sampling

Status: completed
Source specs: docs/specifications/06-Resource_Management.md [RM-5.1]
Superseded by: none

Class: 5 (implementation with spec revision; the runner execution path on the
durable spine changes, so hardening applies). Promotion strategy B (atomic).

## Goal

Make host-runner resource monitoring cost proportional to the task's own
process tree, and make a supervising task with no limit configured quiet,
without losing limit enforcement or the reported metrics.

Two parts, one change:

1. Replace the host-wide parent-PID scan behind
   `psutil.Process.children(recursive=True)` with one descendant walker,
   `weft/core/process_tree.py::descendant_processes`, that asks the kernel
   for one parent's children at a time (macOS libproc, Linux `/proc` task
   children, psutil elsewhere). Metric collection and the kill path both use
   it.
2. Give every runner loop one sampling clock,
   `weft/core/resource_monitor.py::sample_delay`: with any limit configured,
   sample every interval exactly as before; without one, sample on a
   doubling schedule (1, 2, 4, ... intervals), so a supervising task's
   sampling cost grows with the logarithm of its run time while the work
   items that report metrics today still report them.

## Source Documents and Spec Baseline

Spec baseline: `08c2f87b` (`perf/admission-base`), with
`docs/specifications/06-Resource_Management.md` last changed at
`a55f47dd`. Plan type: implementation with spec revision ([RM-5.1]).
Promotion baseline: the commit that lands this plan (spec, code, and tests
land together; strategy B).

Theory read: `docs/program-theory.md` [WT-0], [WT-1], [WT-2] item 4,
[WT-2a], [WT-4] 4.1-4.4, [ALT-WT-002], [ALT-WT-010], [ALT-WT-015],
[ALT-WT-017], [ALT-WT-021]. Governing principles for this change:

- [WT-4.3] "A quiet task costs nothing an operator can see": a task that is
  only supervising is quiet; supervising cost is measured, not inferred.
- [WT-2] item 4: the resource-monitor sample deadline is a legitimate
  protective ceiling of a readiness-driven supervisor.
- [WT-4.4] "Measure; do not bake in inefficiency", "One path on all
  platforms" (platform branches are narrow adapter leaves), "Shared
  function, shared code", "No over-armoring", "No capability diminution".

Agent context read: `docs/agent-context/README.md`, `decision-hierarchy.md`
([DOM-15]), `principles.md`, `engineering-principles.md`,
`runbooks/writing-plans.md`, `runbooks/hardening-plans.md`,
`skills/test-audit/SKILL.md`.

## Verified Premise and Current Structure

Measured on the development Mac (1,507 host processes) with the code in
this change:

| Operation | Before | After |
| --- | --- | --- |
| Descendant walk of a two-process tree | 35.3 ms (`children(recursive=True)`) | 0.08 ms (`descendant_processes`) |
| Full `ResourceMonitor.snapshot()`, one-process tree | dominated by the walk | 0.04 ms |
| Full `ResourceMonitor.snapshot()`, two-process tree | dominated by the walk | 0.09 ms |

The coordinator's experiment (shared brief) replaced the walk with the
worker PID only: task-side `has_pending` fell from 15 ms to 0.9 ms and the
1,250-task PostgreSQL run from 437 s to 300 s. That experiment is not
shippable because it drops grandchildren from memory totals and limit
enforcement; this walker keeps them at nearly the same cost.

Current structure (before this change):

- `weft/core/resource_monitor.py::ResourceMonitor._process_tree` called
  `self._process.children(recursive=True)`; psutil builds a parent map of
  every host process to answer it. `snapshot()` then reads cpu times,
  memory, descriptors, and connections for each process in the tree.
- `check_limits()` returned early only for `limits is None`; the TaskSpec
  always supplies a `LimitsSection` whose fields default to `None`, so every
  default task sampled on every interval.
- Four loops schedule samples, each with `next = now + interval`:
  `HostTaskRunner._run_one_shot_terminal_handoff` (function and agent
  one-shot targets), `run_monitored_subprocess` (command one-shot targets,
  the macOS sandbox and Docker runners), `AgentSession.execute` (persistent
  agent sessions, session-scoped instant), and
  `InteractiveTaskMixin._interactive_flush_outputs` (interactive command
  sessions).
- The kill path, `weft/helpers/__init__.py::_list_process_descendants`,
  also called `children(recursive=True)`.

Metrics flow, read before deciding semantics:
`RunnerOutcome.metrics` (the last sample) reaches
`Consumer._extract_metrics`, which sets `TaskSpec.state` memory, cpu, fds,
and connections with peaks, and the terminal event's `metrics` payload;
TaskMonitor collation exports them as `resources`. Today a work item reports
metrics only when it lived at least one interval: the final
`monitor.snapshot()` runs after the worker is reaped and always fails
(verified: both one-shot paths return `metrics=None` and log a warning for a
0.2 s task, and psutil raises `ZombieProcess` for every metric of an exited,
unreaped child on macOS).

Falsified premise in the brief's example: "one sample at start and one at
end" cannot be honored without a clock. A one-shot command and an
interactive command session end by exiting, and an exited process cannot be
sampled (zombie, verified); a function worker exits right after sending its
result, so an end sample would race its exit. A metrics-only clock is
therefore needed for exit-ending runtimes; the doubling schedule is the
classical answer for sampling something whose end time is unknown: the last
sample is never older than half the run, and the number of samples is
logarithmic in the run's length.

## Context, Files, and Ownership

Files to modify:

- `weft/core/process_tree.py` (new): `descendant_processes` and its
  platform leaves.
- `weft/core/resource_monitor.py`: `_process_tree` uses the walker;
  `limits_configured` and `sample_delay`.
- `weft/core/runners/host.py`, `weft/core/runners/subprocess_runner.py`,
  `weft/core/runners/sessions.py`, `weft/core/tasks/interactive.py`: the one
  sampling clock.
- `weft/helpers/__init__.py`: `_list_process_descendants` uses the walker.
- `weft/core/deferred.py`: `get_ctypes` docstring names its second caller.
- `docs/specifications/06-Resource_Management.md` [RM-5.1].
- Tests: `tests/helpers/process_tree.py` (new), `tests/core/test_process_tree.py`
  (new), `tests/specs/resource_management/test_resource_metrics.py`,
  `tests/tasks/test_runner.py`, `tests/tasks/test_task_interactive.py`.

Reuse, do not duplicate: `weft/core/deferred.py::get_ctypes` for ctypes
(house style: deferred imports only through `deferred.py` accessors); the
existing loader `load_resource_monitor`; the existing loops' deadlines.

Comprehension questions (answer before editing):

1. Why can a function target's end sample not replace the clock?
   Answer: the worker sends its result and exits; by the time the supervisor
   reads the payload the worker may already be a zombie, which psutil cannot
   sample on macOS.
2. What makes the walker's result the same set as psutil's?
   Answer: both follow kernel parent links from the root; the walker keeps
   psutil's identity rule (the root must still be the process it was created
   for; a child older than the root is a reused PID and is neither kept nor
   walked), and, like psutil, reads the root's creation time afresh for that
   comparison rather than the value cached on the root object.

## Invariants, Constraints, and Failure Policy

- With any limit configured (`memory_mb`, `cpu_percent`, `max_fds`,
  `max_connections` not `None`), sampling cadence, `check_limits` logic, the
  4-of-5 CPU rule, single-sample memory/descriptor/connection enforcement,
  and limit outcomes are unchanged.
- The enforcement and kill boundary is the same parent-PID descendant tree;
  grandchildren count toward limits and are killed.
- No TaskSpec, queue, payload, CLI, or public extension contract changes.
  `BaseResourceMonitor` gains no method; custom monitors keep the documented
  protocol and constructor. Every loop chooses the clock from the runner's
  own limits input; no loop reads a `limits` attribute from the monitor
  (review finding F2; `AgentSession` receives the runner's `limits`).
- Races are normal: a process that exits mid-walk is skipped. A failing
  platform source (libproc missing, `CONFIG_PROC_CHILDREN` absent) selects
  psutil once, with a warning; no per-call detection or retry.
- No new dependency, no new constant beyond the libproc type code and the
  initial buffer size, no new execution path.

## Difference Register

| Change | Class | Theory basis |
| --- | --- | --- |
| Descendant enumeration cost follows the task's tree, not the host's process count (35 ms to 0.08 ms) | C | [WT-4.4] Measure; do not bake in inefficiency; [WT-4.3] quiet task |
| Kill path enumerates descendants through the same walker | N | [WT-4.4] Shared function, shared code |
| Platform leaves: macOS libproc, Linux `/proc` task children, psutil elsewhere or when unavailable | N | [WT-4.4] One path on all platforms; narrow adapter leaves |
| With any limit configured: samples every interval, same enforcement | N | none needed |
| Without a limit: samples at 1, 2, 4, ... intervals instead of every interval (a 15 s task: 4 samples instead of 15) | C | [WT-4.3] a supervising task is quiet; cost logarithmic in run time |
| Without a limit: reported metrics come from the last sample, which lies in the latter half of the run instead of the last interval; `cpu_percent` covers the last doubling epoch instead of one interval | D (freshness of the reported value) | [WT-4.3] quiet supervising task; metrics stay present for every work item that reports them today; an operator who needs interval freshness configures any limit. Owner question 1 |
| Work items shorter than one interval still report no metrics | N | unchanged |
| Persistent agent session without a limit: each work item samples at its start (session-scoped instant only while a limit is configured) | C | each item reports its own metrics; nothing is enforced, so a reset postpones nothing |
| Docker runner without a limit: `docker stats` follows the same doubling clock | C | same clock for every monitor; Docker stats calls are the most expensive samples |
| The walker reads the root's creation time afresh for the reused-PID comparison, as psutil does, so a wall-clock correction cannot hide live descendants (review finding F1) | N | [WT-4.4] no capability diminution; equivalence with psutil |
| Persistent agent sessions choose the clock from the runner's limits input (`AgentSession(limits=...)`), not from the monitor; a custom monitor needs no `limits` attribute and need not call the base constructor, as on the baseline (review finding F2) | N | [WT-2a.9] one current contract; the documented monitor protocol ([RM-5.1]) is unchanged |
| Spec text: the old example "calling `os.setsid()`" does not leave a parent-PID tree (verified: psutil and the walker both still list a `setsid` child); removed and stated correctly | N (spec correction) | specs describe what is |

## Proposed Spec Delta

Promotion strategy: B (atomic). Spec text, implementation mapping, code,
reciprocal `Spec:` backlinks, and tests land in one change. The plan's
labelled Replacement blocks below are verbatim copies of the promoted text.

Scope in [RM-5.1]: the implementation-mapping block (add the walker, the
clock, and `get_ctypes`), the rollback paragraph's boundary name ("psutil-
visible" to "parent-PID"), a new bullet after the poll-cadence bullet, the
interactive-session bullet (one added sentence), the persistent-agent-session
bullet (re-arm rule and the no-limit reset), the violation bullet (kill path
walker), and the enforcement-boundary bullet (walker contract, platform
leaves, setsid correction). Top-of-file implementation mapping adds
`weft/core/process_tree.py`; `## Related Plans` adds this plan.

Clause audit of rewritten text: the 1.0 s default cadence, one snapshot and
one `check_limits()` per poll, the interactive rules (stream and control
activity do not move the deadline; timer only with a `monitor_class`;
Debugger and no-monitor sessions wait on events), the agent-session rules
(first sample on the first live pass; never sooner than due; at least one
interval apart; no sampling between items; with a limit, no per-item reset),
the post-exit violation rule, the trust-model statement, and the
double-fork escape all survive. Changed: the clock without a limit, the
agent-session reset without a limit, the walker, and the setsid example.

### [RM-5.1] implementation mapping, additions

Replacement (`docs/specifications/06-Resource_Management.md`):
```markdown
`weft/core/resource_monitor.py::limits_configured`,
`weft/core/resource_monitor.py::sample_delay` (the one sampling clock, used by
`weft/core/runners/host.py::HostTaskRunner._run_one_shot_terminal_handoff`,
`weft/core/runners/subprocess_runner.py::run_monitored_subprocess`,
`weft/core/runners/sessions.py::AgentSession.execute`, and
`weft/core/tasks/interactive.py::InteractiveTaskMixin._interactive_flush_outputs`);
`weft/core/process_tree.py::descendant_processes` (the one descendant walk,
used by `ResourceMonitor._process_tree` and
`weft/helpers/__init__.py::_list_process_descendants`),
`weft/core/deferred.py::get_ctypes`;
```

### [RM-5.1] new bullet after the poll-cadence bullet

Replacement (`docs/specifications/06-Resource_Management.md`):
```markdown
- Every runner loop schedules its next poll with one clock,
  `sample_delay` in `weft/core/resource_monitor.py`. While any limit is
  configured (`memory_mb`, `cpu_percent`, `max_fds`, or `max_connections`
  is set), polls are one cadence apart, so enforcement is unchanged by this
  clock. The clock reads the runner's limits input, never the monitor, so the
  monitor protocol is unchanged. Without a configured limit nothing is
  enforced and polls serve only
  the reported metrics: the delay to the next poll equals the time already
  monitored, never less than one cadence, so polls fall at one, two, four,
  ... cadences. A supervising task with no limit therefore polls a number of
  times logarithmic in its run time, and the last poll before its runtime
  exits lies in the latter half of the run. The reported metrics are those of
  the last poll; its `cpu_percent` is averaged over the time since the poll
  before it, or since monitoring began. A one-shot work item that ends before
  its first poll reports no metrics.
```

### [RM-5.1] interactive command session bullet

Replacement (`docs/specifications/06-Resource_Management.md`):
```markdown
- For an interactive command session, Consumer publishes the next resource
  sample through `next_wait_timeout()` and calls the session's `poll_limits()`
  only when that absolute deadline is due. Stream, control, backend, and process
  exit activity do not move the resource-sampling deadline or cause additional
  samples. Each new deadline follows `sample_delay`, measured from when the
  session's sampling began. The sampling timer exists only for a session
  started through `TaskRunner` whose TaskSpec names a `monitor_class`. The
  built-in Debugger's owner-local session bypasses `TaskRunner` and publishes
  no sampling timer. A session with no monitor class likewise waits on events
  alone.
```

### [RM-5.1] persistent agent session bullet

Replacement (`docs/specifications/06-Resource_Management.md`):
```markdown
- A persistent agent session samples the same way, at its monitor's
  `polling_interval`: `AgentSession` holds one session-scoped next-sample
  instant, takes its first sample on its first observation pass with a live
  producer, samples on any later observation pass once that instant is due
  and never sooner, re-arms the instant `sample_delay` after a clock reading
  taken after each sample, measured from the work item's start, and
  publishes the instant as a deadline of its handoff wait. Sampling runs
  while a work item is being supervised: the deadline bounds the requested
  wait, and the spacing between samples, measured after each sample, is at
  least one interval. Between work items no sample is taken. With a limit
  configured, a new work item neither resets nor postpones the instant, so
  an instant that fell due between items is honored on the first
  observation pass of the next work item. Without one, each work item makes
  its first observation pass a sample, so every item reports metrics taken
  while it ran.
```

### [RM-5.1] violation bullet

Replacement (`docs/specifications/06-Resource_Management.md`):
```markdown
- On violation, the runner escalates through `terminate_process_tree` /
  `kill_process_tree` (`weft/helpers/__init__.py`), which walk the process
  tree from the worker PID with the same descendant walk as metric
  collection (below). In the shared one-shot command subprocess supervisor
  (`run_monitored_subprocess`), a violation reported after the supervised
  process has already exited is not a confirmed limit: the supervisor re-polls
  the process before escalating, kills nothing, and the exit code decides the
  outcome. The function-runner and persistent-session paths keep their own
  precedence rules in [CC-2.5].
```

### [RM-5.1] enforcement-boundary bullet (through the setsid correction)

Replacement (`docs/specifications/06-Resource_Management.md`):
```markdown
- **Enforcement boundary**: both metric collection (`_process_tree` in
  `weft/core/resource_monitor.py`) and the kill path walk the *parent-PID
  descendant tree* rooted at the worker PID through one walker,
  `descendant_processes` in `weft/core/process_tree.py`. It keeps the
  contract of psutil's `children(recursive=True)`: a root that is no longer
  the process it was created for raises `NoSuchProcess`, a child older than
  the root (whose creation time is read afresh, so a wall-clock correction
  since the root was first observed cannot hide live children) is a reused
  PID and is skipped, and a process that exits during the walk is skipped. It asks the kernel for one parent's children at a
  time, so its cost follows the task's own tree rather than the host's
  process count: macOS reads libproc `proc_listpids(PROC_PPID_ONLY)`, Linux
  reads `/proc/<pid>/task/<tid>/children` for every thread of the parent
  (the kernel records a child under the thread that forked it; this needs
  `CONFIG_PROC_CHILDREN`), and other platforms, or a platform whose source
  is unavailable, use psutil's own walk. A process that leaves that tree —
  e.g. by double-forking so the intermediate parent exits and the grandchild
  is reparented outside the tracked tree — is no longer enumerated and is
  invisible to both limit checks and the kill escalation. Starting a new
  session with `os.setsid()` alone does not change the parent PID and does
  not leave the tree. This is
```

## Rollout, Rollback, and Non-Goals

Library change; no queue, schema, or payload migration. Rollback is a code
revert of this commit; it restores the host-wide scan and interval sampling
for unlimited tasks. No one-way door. Reverting only part 2 is one line in
`sample_delay` (return `interval` unconditionally) plus the agent-session
reset and the spec bullet.

Not in scope (owned elsewhere or recorded as follow-up):

- The 50 ms `ACTIVE_CONTROL_POLL_INTERVAL` re-check of the pull-style
  `cancel_requested` predicate, which every Consumer-driven supervisor still
  runs; it is the larger remaining wake source of a supervising task and is
  a recorded owner follow-up (`weft/_constants.py` docstring).
- The final post-reap `snapshot()` attempts in both one-shot paths, which
  always fail and log a warning for work shorter than one interval
  (pre-existing; unchanged).
- `oneshot()` batching of per-process psutil reads, Windows enumeration, and
  any change to `ResourceMetrics` fields.

## Implementation Tasks

1. Walker. Add `weft/core/process_tree.py::descendant_processes` with the
   psutil contract and the three leaves; select the leaf once
   (`functools.cache`). Load ctypes through `get_ctypes`. Stop if the walker
   wants a cache of processes across samples or a second walker.
2. Use the walker in `ResourceMonitor._process_tree` and
   `weft/helpers/__init__.py::_list_process_descendants` (catch `OSError`
   as well as `psutil.Error` there, since a native leaf raises `OSError`).
3. Clock. Add `limits_configured` and `sample_delay` to
   `weft/core/resource_monitor.py`; use `sample_delay` at each of the four
   re-arm sites, always with the runner's own limits input (pass
   `limits=self._limits` to `AgentSession` from
   `HostTaskRunner.start_agent_session`; never read limits from the
   monitor); in `AgentSession.execute`, reset the instant per work item
   only when no limit is configured. Stop if any limited-path cadence
   changes.
4. Tests (below), then spec text, mapping, and backlinks (strategy B).

## Testing Plan

No mocks of process lifecycle; real spawned trees from
`tests/helpers/process_tree.py` (each non-leaf forks one child from its main
thread and one from a live worker thread; leaves may hold incompressible
memory).

- `tests/core/test_process_tree.py`: two-level, thread-forked tree compared
  with psutil on the running platform, with psutil's host-wide scan made
  unavailable on macOS and Linux to prove the leaf is used; a root object
  whose cached creation time is an hour later than the kernel's (as after a
  backward clock correction) still yields every descendant psutil yields;
  an exited root raises `NoSuchProcess`.
- `tests/specs/resource_management/test_resource_metrics.py`: a snapshot of
  a chain whose grandchild holds 128 MiB reports at least 128 MiB (replaces
  a test that mocked `psutil.Process` and asserted the
  `children(recursive=True)` call).
- `tests/tasks/test_runner.py`:
  - a 120 MiB memory limit fires on a grandchild's 160 MiB (root and child
    measure about 41 MiB);
  - the sampling clock, for command and function targets, with and without
    a limit: without one, 3 to 8 samples in a 2 s run and metrics still
    reported; with one, at least 10 (about 40 nominal);
  - a persistent agent session without a limit samples every work item;
  - `TaskRunner.start_agent_session` with a custom monitor that keeps its
    limits private and skips the base constructor executes and reports
    metrics.
  - Fixture changes: the agent-session cadence tests pass a configured limit
    to the session through `_spawn_agent_session_for_target(limits=...)`
    (their contract is enforcement cadence); the monitor fixtures are as on
    the baseline. One mock-based kill-path test stubs `descendant_processes`
    instead of `root.children`.
- `tests/tasks/test_task_interactive.py`: the existing interval-reschedule
  test configures a limit; a new test proves the doubling reschedule without
  one.
- Existing enforcement tests that must stay green unchanged:
  `test_task_runner_enforces_memory_limit`, `..._cpu_limit`,
  `..._fd_limit`, `..._reports_multiple_violations`,
  `test_task_runner_timeout_terminates_command_descendants`,
  `test_task_runner_agent_limit_violation`,
  `test_interactive_limit_marks_killed_without_terminal_transition_error`.

Sensitivity checks performed: with recursion removed from the walker the
tree test fails (2 of 6 descendants); with the clock forced to the interval
the no-limit cases fail (19 and 25 samples against a cap of 7 at 1 s); with
the monitor limited to root plus first descendant the grandchild-limit test
times out and the snapshot test reads 40.9 MiB; without the agent-session
reset the per-item test sees 1 sample for 5 items.

## Verification Commands and Acceptance

```bash
./.venv/bin/python -m pytest -o addopts="" -m "not slow" -n 4 \
  tests/core/test_process_tree.py tests/tasks/test_runner.py \
  tests/core/test_subprocess_runner.py tests/specs/resource_management \
  tests/tasks/test_task_interactive.py tests/tasks/test_agent_execution.py \
  tests/commands/test_task_commands.py tests/tasks/test_consumer_terminal_events.py
PYTEST_XDIST_AUTO_NUM_WORKERS=4 ./bin/pytest-pg <same targets>
./.venv/bin/ruff check .
./.venv/bin/ruff format --check <touched files>
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django \
  extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox \
  extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/python bin/check-doc-paths
```

Runtime observation for the coordinator's benchmark: per-task sampling
cost should fall from tens of milliseconds of GIL-held psutil work per
second to well under a millisecond per sample, and an unlimited task's
sample count should be about log2 of its run time in seconds.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| [RM-5.1] | Brief's example: one sample at start and one at end without a clock | Doubling metrics-only clock | An exited runtime cannot be sampled (verified zombie behavior); see Verified Premise | Promoted in this change |

## Open Owner Questions

1. No-limit metrics freshness. Implemented: doubling clock (quiet,
   logarithmic, metrics present for the same work items as today, value up
   to half a run old). Alternatives: (a) keep interval sampling for
   unlimited tasks; after the walker a sample costs 0.04-0.09 ms, so this is
   N for metrics but keeps a 1 Hz supervision timer; (b) no sampling without
   a limit, which loses metrics for most commands (D). The difference
   between the implemented clock and (a) is not expected to be measurable in
   the admission benchmark.
2. Whether the 50 ms cancel-predicate re-check should be scheduled next,
   since it now dominates a supervising task's wakeups.

## Review and execution record

Author family: Anthropic (Claude). Cross-family review: to be filled by the
coordinator.

Fresh-eyes self-review (author, after drafting): checked that every limited
path keeps `interval` (the helper returns it whenever any field is set,
including `max_connections=0`); that the agent-session reset touches only
the no-limit case; that duck-typed test monitors without `limits` exist only
in tests (production session monitors come from `load_resource_monitor`,
which requires a `BaseResourceMonitor` subclass); that Docker's monitor is
scheduled by the `limits` argument of `run_monitored_subprocess`, not by a
`limits` attribute it lacks; that no new constant duplicates an existing
one; and that the spec bullets match the code (verified rule by rule).
One harness correction during verification: the limited-cadence bound
(`>= 12` samples in 1 s) failed once under four workers; latency only lowers
the interval count, so the run was lengthened to 2 s with a bound of 10
against about 40 nominal, keeping the no-limit cap of 8 against about 6.

### Implementation evidence, 2026-11-08

Branch `perf/monitor-scan` from `perf/admission-base` (`08c2f87b`), macOS
host, four xdist workers throughout.

- SQLite: `tests/core/test_process_tree.py tests/tasks/test_runner.py
  tests/core/test_subprocess_runner.py tests/specs/resource_management
  tests/tasks/test_task_interactive.py tests/tasks/test_agent_execution.py
  tests/commands/test_task_commands.py tests/tasks/test_consumer_terminal_events.py`:
  489 passed, 10 skipped.
- PostgreSQL (`./bin/pytest-pg`, same targets): 489 passed, 21 skipped.
- Kill-path callers (`tests/test_harness_registration.py
  tests/tasks/test_task_execution.py tests/tasks/test_command_runner_parity.py
  tests/tasks/test_runtime_identity_signals.py
  tests/tasks/test_runtime_identity_custody.py
  tests/core/test_manager_start_observer_failure.py tests/core/test_manager.py`,
  plus `tests/tasks/test_task_stop_sqlite_only.py` on SQLite): SQLite 654
  passed, 3 skipped; PostgreSQL 614 passed, 12 skipped.
- After the final docstring and registry edits: `tests/core/test_process_tree.py
  tests/specs tests/tasks/test_runner.py`: 385 passed.
- `ruff check .`, `ruff format --check` on touched files, the repo mypy
  command (502 source files), and `bin/check-doc-paths`: clean.
- Linux leaf: `weft/core/process_tree.py` loaded in a `python:3.12-slim`
  container (kernel 7.2.12) with a `/proc`-backed psutil stand-in selected
  the `/proc` leaf, matched the parent-PID map for a root with a child forked
  by a live worker thread, and raised `NoSuchProcess` for an exited root.
  CI exercises the Linux leaf through `tests/core/test_process_tree.py`.

### Cross-family review, round 1

Tier 1, Codex (codex exec -s read-only, reasoning effort high), 2026-11-08,
scoped-change review of plan+implementation at bb475514 against 08c2f87b.
Verdict: blocker: F1, F2.

The reviewer judged the freshness D row (no-limit metrics come from a
sample in the latter half of the run) consistent with [WT-4.3].

| ID | Severity | Finding | Disposition |
| --- | --- | --- | --- |
| F1 | P2 | `weft/core/process_tree.py`: the walker compared the root's cached creation time with fresh child creation times; after a backward wall-clock correction live descendants look older than the root and were skipped as reused PIDs, dropping them from metrics and limit enforcement. psutil re-reads the root's time at that point (reproduced with a controlled clock-skew probe). | Accepted, fixed: `descendant_processes` keeps the `is_running()` identity check and then reads the comparison time from a fresh `psutil.Process(root.pid).create_time()`, as psutil's `children()` does; [RM-5.1] states the fresh read. New test `tests/core/test_process_tree.py::test_descendant_processes_survives_a_stale_root_creation_time` gives the root object a cached creation time one hour later than the kernel's and requires psutil's set; at bb475514 it fails with `assert set() == {<child>, <grandchild>}`. |
| F2 | P2 | `weft/core/runners/sessions.py`: persistent sessions read `self._monitor.limits`, which the documented monitor protocol ([RM-5.1]) does not require; a custom monitor that keeps limits privately or skips the base constructor worked on the baseline and raised `AttributeError` after the execute request was enqueued. | Accepted, fixed: `AgentSession` takes the runner's own `limits` input (`HostTaskRunner.start_agent_session` passes `limits=self._limits`) and both the per-item reset and `sample_delay` use it; no loop reads limits from a monitor. [RM-5.1] states that the clock reads the runner's limits input. New test `tests/tasks/test_runner.py::test_task_runner_agent_session_accepts_a_monitor_without_limits_attribute` loads such a monitor through `monitor_class` and executes a work item; at bb475514 it fails with `AttributeError: '_PrivateLimitsMonitor' object has no attribute 'limits'` at `sessions.py:425`. The test fixtures that gained a `limits` attribute in bb475514 are restored to the baseline; cadence tests pass the limit to the session instead. Difference-register row added. |

Verification after the fixes (four xdist workers): SQLite `tests/core/test_process_tree.py
tests/tasks/test_runner.py tests/specs tests/tasks/test_task_interactive.py
tests/tasks/test_agent_execution.py tests/core/test_subprocess_runner.py`:
554 passed, 10 skipped; PostgreSQL (`./bin/pytest-pg`, same targets): 554
passed, 21 skipped; `ruff check .`, `ruff format --check` on touched files,
the repo mypy command (502 source files), and `bin/check-doc-paths`: clean.

### Cross-family review, round 2

Tier 1, Codex (codex exec -s read-only, reasoning effort high), 2026-11-08,
round-2 verification scoped to F1 and F2 over `bb475514..8dd29fe0`.
Verdict: PASS, no new defect in the fix delta. The reviewer confirmed the
fresh root timestamp with a live two-level tree probe (baseline returned no
descendants, the fix returned both) and reproduced the F2 `AttributeError`
against the baseline methods before passing the candidate.
