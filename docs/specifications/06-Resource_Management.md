# Resource Management and Error Handling

This document describes the current resource-monitoring and error-handling
contract. The current system favors simple, explicit termination and visible
failure over complex partial mitigation.

_Implementation snapshot_: resource limits are enforced at the runner
boundary. The default host-path monitor is psutil-based, but runner plugins
may also map limits into native runtime controls. Limit violations that Weft can
classify become task failures visible through state, control, and
reserved-queue behavior.

_Implementation mapping_: `weft/core/resource_monitor.py`,
`weft/core/process_tree.py`,
`weft/core/runners/host.py`, `weft/core/runners/subprocess_runner.py`,
`weft/core/tasks/runner.py`, `weft/core/tasks/base.py`,
`weft/core/tasks/consumer.py`, `weft/ext.py`, `weft/_runner_plugins.py`,
`extensions/weft_microsandbox/weft_microsandbox/_options.py`,
`extensions/weft_microsandbox/weft_microsandbox/_runtime.py`.

See also:

- planned companion:
  [`06A-Resource_Management_Planned.md`](06A-Resource_Management_Planned.md)
- system guarantees:
  [`07-System_Invariants.md`](07-System_Invariants.md)

## Resource Management [RM-0]

Current Weft resource management is monitor-and-react at the runner boundary:

- sample live process metrics
- compare against configured limits
- terminate when a hard violation is confirmed
- surface the failure durably

The reason for this design is clarity. Hard termination is easier to explain and
audit than a mix of throttling, grace-period heuristics, and silent recovery.

### 1. Memory Management [RM-1]

Current behavior:

- memory usage is sampled from the runtime process tree on psutil-backed
  runtimes and from runner-native stats on Docker-backed runtimes
- configured memory limits are treated as hard limits: "hard" describes the
  response (terminate on a confirmed violation, no throttling), not an
  instantaneous ceiling. Host (psutil) detection is bounded by the sample window,
  so a fast allocator can transiently exceed `memory_mb` between samples before
  termination; Docker-backed runners map the limit to a native runtime quota;
  the Microsandbox runner passes `memory_mb` through to the Microsandbox SDK
  when used.
- host and Docker memory violations surface as limit failures. Microsandbox
  native memory enforcement currently lacks a reliable OOM classification path
  in the runner, so guest memory-pressure termination can surface as an ordinary
  runner error until SDK metrics or a native OOM signal are wired into outcome
  mapping.

_Implementation mapping_:
`weft/core/resource_monitor.py::ResourceMonitor.check_limits`;
`weft/core/taskspec/model.py::LimitsSection` field memory_mb.

### 2. CPU Management [RM-2]

Current behavior:

- CPU usage is evaluated over a rolling sample window on psutil-backed
  runtimes
- the host path treats sustained over-limit CPU as a hard violation
- Docker-backed runners map CPU limits into native runtime quotas instead of
  relying on the psutil window
- the Microsandbox runner converts `cpu_percent` into an SDK CPU allocation
  for the sandbox instead of relying on a host psutil process tree
- the response is termination, not throttling

_Implementation mapping_:
`weft/core/resource_monitor.py::ResourceMonitor._is_sustained_cpu_violation`,
`weft/core/resource_monitor.py::ResourceMonitor._get_average_cpu`;
`weft/core/taskspec/model.py::LimitsSection` field cpu_percent.

### 3. File Descriptor Management [RM-3]

Current behavior:

- open-file counts are sampled when the backend can provide them
- psutil-backed runtimes enforce configured fd limits as hard limits
- Docker-backed runners map fd limits into native runtime ulimits
- the Microsandbox runner maps `max_fds` into an SDK `nofile` rlimit

_Implementation mapping_:
`weft/core/resource_monitor.py::ResourceMonitor._open_file_count`;
`weft/core/taskspec/model.py::LimitsSection` field max_fds.

### 4. Network Connection Management [RM-4]

Current behavior:

- connection counts are sampled when the backend can provide them
- psutil-backed runtimes enforce configured connection limits as hard limits
- Docker-backed runners currently support only `max_connections=0`, which maps
  to network isolation; other values are rejected
- the Microsandbox runner follows the same first-slice policy:
  `max_connections=0` maps to `runner.options.network="none"`, explicit
  `network="allow"` conflicts with `max_connections=0`, and non-zero
  `max_connections` values are rejected

_Implementation mapping_:
`weft/core/resource_monitor.py::ResourceMonitor._connection_count`;
`weft/core/taskspec/model.py::LimitsSection` field max_connections.

## Resource Monitoring Implementation [RM-5]

Resource monitoring lives at the runner boundary rather than in a separate
global service.

Why:

- limits apply to concrete runtimes
- monitoring ownership should stay close to runtime creation and teardown
- runner plugins also own runtime-specific stop, kill, and describe hooks
- alternate runners may need alternate monitoring backends or native runtime
  controls

### Default psutil-Based Monitor [RM-5.1]

The default and only built-in psutil monitor class is
`weft.core.resource_monitor.ResourceMonitor`. The monitor protocol is
`start()`, `stop()`, `snapshot()`, `check_limits()`, and `last_metrics`.
Custom monitor classes use that protocol and one documented keyword
constructor accepting the current limits and polling interval. The loader
invokes that constructor once. Constructor `TypeError` and other implementation
errors propagate; they are not reinterpreted as evidence for an older
signature.

The built-in monitor imports the required psutil dependency directly and uses
its supported `net_connections()` API. Platform-specific file descriptor,
handle, and process-disappearance behavior remains part of the current monitor
implementation.

_Implementation mapping_:
`weft/ext.py::ResourceMetrics`,
`weft/core/resource_monitor.py::BaseResourceMonitor`,
`weft/core/resource_monitor.py::ResourceMonitor`,
`weft/core/resource_monitor.py::load_resource_monitor`,
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
`weft/core/runners/host.py::HostTaskRunner.run_with_hooks`,
`weft/core/runners/host.py::HostRunnerPlugin.stop`,
`weft/core/runners/host.py::HostRunnerPlugin.kill`,
`weft/core/runners/host.py::HostRunnerPlugin.describe`;
`weft/core/runners/subprocess_runner.py::run_monitored_subprocess` owns the
post-spawn acquisition/rollback scope, `_PipeAdapter` owns cancellable pipe
I/O and close, `_stop_and_join_pipe_adapters` shares adapter retirement between
normal and exceptional cleanup, `_drain_stream_queue` owns finite per-stream
work, and `_finish_streams` owns bounded final capture and complete frozen-backlog
delivery;
`weft/ext.py::RunnerPlugin`, `weft/ext.py::RunnerHandle`;
`weft/_runner_plugins.py::get_runner_plugin`,
`weft/_runner_plugins.py::require_runner_plugin`.

Current responsibilities:

- start and stop monitoring for a live runtime
- capture the latest resource metrics
- timestamp metrics locally without opening broker queues from runner or
  worker threads
- compare those metrics to configured limits
- retain recent metrics so the task can publish peak or recent values
- surface runner-specific stop/kill/describe hooks through the active plugin

After successful process creation, the one-shot command runner owns rollback
for later acquisition failures, including monitor loading and pipe-adapter
setup. Before propagating such a failure, it stops and reaps the acquired root
process and releases its started adapters and endpoints using the runtime's
existing stop/kill hooks. The original failure remains primary. Monitor-class
validation and constructor failures are not converted into successful
execution without monitoring. This obligation does not broaden the
parent-PID process-tree enforcement boundary below.

Enforcement mechanics and security boundary:

- The default poll cadence is 1.0s (`DEFAULT_POLLING_INTERVAL` in
  `weft/_constants.py`, threaded through as `spec.polling_interval` and
  `run_monitored_subprocess`'s `monitor_interval`). Each poll takes one
  `ResourceMetrics` snapshot and calls `check_limits()`.
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
- Memory and file-descriptor/connection limits are enforced on a **single
  sample**: `ResourceMonitor.check_limits` kills the runtime the first
  poll where `metrics.memory_mb` (or `open_files`/`connections`) exceeds the
  configured limit — there is no grace window or debounce for memory.
- CPU is the one exception: `_is_sustained_cpu_violation` requires at least 5
  recorded samples and kills only when 4 of the last 5 samples exceeded
  `cpu_percent` (a "4-of-5" rule), which smooths over short spikes that
  memory enforcement does not tolerate.
- On violation, the runner escalates through `terminate_process_tree` /
  `kill_process_tree` (`weft/helpers/__init__.py`), which walk the process
  tree from the worker PID with the same descendant walk as metric
  collection (below). In the shared one-shot command subprocess supervisor
  (`run_monitored_subprocess`), a violation reported after the supervised
  process has already exited is not a confirmed limit: the supervisor re-polls
  the process before escalating, kills nothing, and the exit code decides the
  outcome. The function-runner and persistent-session paths keep their own
  precedence rules in [CC-2.5].
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
  consistent with the trust model in
  [00-Overview_and_Architecture.md](00-Overview_and_Architecture.md)
  ("Observability and Security"): Weft assumes user-level trust, not hostile
  multi-tenancy. The OS/filesystem is the security boundary, not a
  resource-monitor sandbox (see also `CLAUDE.md`/`AGENTS.md` §4.11): resource
  limits are a cooperative operational control, not an isolation mechanism
  against an adversarial task.

### Timeout Boundary [RM-5.2]

`spec.timeout` is a work-execution timeout. It applies once the runner or
persistent session is executing a concrete work item. It does not define every
private startup, readiness, or teardown budget used inside the runtime.

For one-shot command execution through the shared subprocess supervisor,
delivering the work item's stdin is part of execution. The execution clock
starts no later than input delivery, and input backpressure must not prevent
cancellation, timeout, or resource-limit supervision. Subprocess pipe I/O is
owned by an opaque platform readiness adapter behind the supervisor reactor;
while execution is active, the supervisor waits on platform pipe readiness
bounded by the next scheduled supervision deadline rather than polling pipes
on a fixed timer, and cancellation or teardown must not depend on another
thread acquiring a buffered I/O lock held by a blocked operation.

Root process completion and output-pipe EOF are separate observations. Once
root completion is accepted, the supervisor performs a bounded post-exit
capture pass over `stdout` and `stderr`: capture finishes immediately when
both streams reach EOF or have no remaining buffered bytes in the kernel pipe,
and is bounded by the private stream-drain budget when an inherited
descendant continues writing. The session then closes its owned parent pipe endpoints, freezing a finite set of captured output, and delivers
that captured output before forming its returned outcome; capture-budget
expiry must not discard output already captured in memory. The same capture,
endpoint-release, and delivery sequence follows the existing runtime
stop/kill hooks for cancellation, timeout, and resource-limit outcomes,
preserving those outcomes.

The capture budget is not a total result-delivery or teardown timeout. Delivery
duration depends on captured volume and sink throughput and may exceed the
private budget. Output produced after capture is stopped is not promised.
Budget expiry neither converts a completed root into an execution timeout nor
asserts physical EOF. Missing physical EOF remains a bounded diagnostic, with
no new terminal state or outcome field.

On Linux, subprocess supervision requires Linux 5.3 or newer and permission
to open pidfds. When Python provides os.pidfd_open, the supervisor uses it.
If that attribute is absent, the supervisor attempts the exported libc
pidfd_open function through ctypes. This compatibility binding opens the same
kernel exit descriptor; it introduces no helper thread, polling fallback,
or alternate reaping owner. It does not retry a failed Python pidfd_open call
through libc, and it does not invoke raw architecture-specific syscall numbers.

If Python lacks os.pidfd_open and either ctypes or libc's exported pidfd_open
symbol is unavailable, supervision raises RuntimeError identifying the unavailable
binding, chained from the actual acquisition
error. Kernel ENOSYS or security-policy EPERM/EACCES raises RuntimeError
identifying the syscall support or permission requirement, chained from the
actual syscall error. Other loader and syscall errors propagate unchanged.
ProcessLookupError means the child is already gone and requests an immediate
wake. A cached process returncode bypasses PID lookup for an already-reaped
child. Live-child exit registration still precedes the supervisor's poll.

While execution is active, output drainage performs finite work per stream
before returning to other streams and supervision, and slow downstream sinks
apply pipe backpressure rather than buffering child output without bound in
parent memory. After capture is frozen, delivery drains the captured chunks
without idle waits. The 64 KiB per-stream active in-flight accounting includes undecoded raw
codec input and queued chunks' consumed bytes. Newline recognition may
also hold one CR character in fixed-size state. Consumed bytes that produce no
text (for example `errors="ignore"`) release their capacity immediately.
An incremental codec retaining 64 KiB without completing its sequence raises
`UnicodeError` with a decoder-buffer-limit diagnostic, including during terminal
capture. Valid stateful encodings whose single sequence exceeds this limit are
rejected rather than buffering without bound or deadlocking on backpressure.
Command stdout decoding preserves CRLF, bare CR and LF, including across split reads. This applies to supervised capture and host interactive sessions. Supervised and host interactive stdin writers encode supplied text without platform newline substitution, so preserved CRLF is not doubled in a command pipeline. The configured text codec, error handling, input budgets and backpressure remain unchanged; stderr diagnostic decoding retains its current policy. This is a text-preservation contract, not a promise to transport arbitrary undecodable bytes losslessly.
These rules do not preempt an arbitrarily blocking callback. A callback exception follows the existing execution-failure cleanup
and exception-precedence contract; it does not guarantee delivery to the
failed callback. Existing Consumer shutdown rules still govern a publisher
that has stopped accepting worker events.

Implementation mapping: `weft/core/runners/subprocess_runner.py::_OutputStreamState`
and `_StdinStreamState` preserve supplied newline characters;
`weft/core/runners/host.py::HostTaskRunner.start_session` configures its text pipes.
Plan: [Command stdout preservation](../plans/2026-11-05-command-stdout-preservation-plan.md).

Current behavior:

- one-shot command and function targets use `spec.timeout` to bound their
  concrete execution work
- one-shot Microsandbox command and agent targets pass `spec.timeout` to the
  Microsandbox SDK execution call and still report timeout through the normal
  task lifecycle
- persistent agent sessions use `spec.timeout` for each session `execute` call
  after the private session worker is ready
- persistent agent session startup uses a separate internal readiness timeout
  for the private `ready` message, because import/plugin/provider startup and
  work execution fail for different reasons
- private startup and readiness failures should report bounded runner
  diagnostics such as startup phase, child PID, exit code, liveness, timeout
  budget, and last private handshake when available. These diagnostics explain
  timeout/error handling but do not change the work-execution timeout contract.

Timeout terminal state and reserved-queue error policy still use the normal
task lifecycle: when work execution exceeds `spec.timeout`, the task reports a
timeout and applies `reserved_policy_on_error`.

Before emitting `timeout_requested` in a host-runner observation turn, the
one-shot adapter consumes a terminal payload that is already ready on the
private response channel. The persistent-session adapter preserves its current
boundary by accepting a due timeout before a same-turn ready payload. Once the
reducer accepts timeout, timeout remains the terminal outcome while the adapter
stops, drains, and cleans up; a later payload does not replace it. In both
policies cancellation is first, confirmed resource limit follows both timeout
and payload, and the first accepted stop remains final.

_Implementation mapping_: `weft/core/runners/host.py` owns the one-shot timeout
observation boundary; `weft/core/runners/sessions.py` owns the persistent-session
boundary; both route observations through `weft/core/terminal_handoff.py`.
`weft/core/runners/subprocess_runner.py` owns the shared subprocess supervisor
(`run_monitored_subprocess`), its platform readiness adapter
(`_open_process_io`: `_PosixSelectorProcessIO` and
`_WindowsOverlappedProcessIO`), and supervised process construction
(`open_supervised_process`).
`weft/core/runners/subprocess_runner.py::_open_child_exit_source` owns Linux
pidfd binding selection and the kernel exit source;
`weft/core/deferred.py::get_ctypes` loads the optional ctypes dependency for
the libc binding.

## Error Handling

### Unified Reservation Pattern

The current error-handling model is queue-first:

- in-flight work is visible in reserved queues
- successful completion clears or finalizes reserved state
- failures leave enough state behind for inspection or explicit recovery

This exists so failure never disappears into a private runtime path.

### Error Categories and State

Current terminal error classes exposed through state include:

- ordinary failure
- timeout
- limit violation
- external stop/kill

The CLI and status surfaces reconstruct these from task-log events and task
state rather than from a separate error database.

### Recovery Boundary

Current recovery is explicit and operator-driven:

- inspect reserved queues
- move messages manually with queue commands
- rerun work intentionally

There is no built-in automatic retry or recovery framework in the current
contract.

## Security Considerations

### Command Execution Safety

Weft validates task shape and runner capability, but it does not currently
enforce a global command allowlist. The trust model is user-level operation in
an already trusted environment.

### Resource Isolation

Current resource isolation is observe-and-react on the host path, with
backend-specific controls in some runner plugins. The contract does not promise
a universal cgroup or container sandbox, and it does not require every runner
to expose the same native controls.

The Microsandbox runner is a stronger optional process/filesystem boundary for
disposable workloads, but it is still a runner-specific backend. Its default
network policy is `none`, its default workspace policy is `none`, and
unsupported limit or workspace requests fail validation instead of silently
widening access. This is an isolation boundary only; it is not semantic
protection against prompt injection or malicious output.

### Environment Variables

Current runners merge environment data needed for execution, but there is no
strong protected-variable policy in the canonical contract yet. Callers should
not assume that the runtime sanitizes or rejects every risky environment
override.

### Agent Constraints

Agent tasks are constrained primarily by TaskSpec shape, runner capability, and
the configured runtime/tool set. There is no current global registry/allowlist
contract for agent actions.

### Large Output Safety

Large task output handling is current behavior, but it is owned by the task
runtime rather than by a generic broker wrapper. Output spill paths therefore
need to be treated as task-runtime artifacts, not as arbitrary caller-supplied
filesystem destinations.

## Scope Boundary

Soft limits, throttling, explicit sandboxing, richer validation helpers, and
automatic retry patterns live in the companion doc. Backend-specific native
controls stay here only when they are already shipped and observable:

- [`06A-Resource_Management_Planned.md`](06A-Resource_Management_Planned.md)

## Related Plans

- retired: `2026-10-27-runtime-and-test-repair-plan` (source 0ff8cfbf; see `docs/plans/README.md`) describes command-lifecycle ownership, pending-index reclamation, and test-sensitivity repair design.
- retired: `2026-10-29-pr48-reactor-and-test-hygiene-repair-plan` (source 0ff8cfbf; see `docs/plans/README.md`) replaces timer-polled subprocess pipe supervision with the platform readiness adapter (`[RM-5.2]`).
- retired: `2026-11-02-reactor-edge-timer-floors-plan` (source 0ff8cfbf; see `docs/plans/README.md`) replaces fixed timer floors on agent-session and interactive waits with their real event sources and samples persistent agent sessions at the monitor's `polling_interval` ([RM-5.1], [RM-5.2]).
- [Linux libc pidfd binding](../plans/2026-11-05-linux-libc-pidfd-binding-plan.md) adds the libc/ctypes binding when Python omits its wrapper ([RM-5.2]).
- [Resource monitor descendant scan](../plans/2026-11-08-resource-monitor-descendant-scan-plan.md) walks only the task's own process tree and samples on a doubling clock when no limit is configured ([RM-5.1]).

- retired: 2026-09-19-interactive-source-integration-plan — source 396c3b85; see docs/plans/README.md

- retired: 2026-09-11-public-python-contracts-plan — source 396c3b85; see docs/plans/README.md

- retired: 2026-08-10-canonical-contract-and-dead-code-cleanup-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-08-08-terminal-handoff-adapter-refactor-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-08-08-subprocess-and-docker-provider-lifecycle-refactor-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-08-01-terminal-handoff-reducer-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-07-02-runtime-correctness-and-retention-remediation-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-06-17-microsandbox-runner-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-29-reliability-and-doc-fixes-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-08-agent-session-and-task-startup-observability-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-04-13-spec-corpus-current-vs-planned-split-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-04-06-runner-extension-point-plan — source 396c3b85; see docs/plans/README.md
- retired: 2026-05-15-task-reactor-and-evidence-worker-plan — source 396c3b85; see docs/plans/README.md

## Related Documents

- [`01-Core_Components.md`](01-Core_Components.md)
- [`07-System_Invariants.md`](07-System_Invariants.md)
