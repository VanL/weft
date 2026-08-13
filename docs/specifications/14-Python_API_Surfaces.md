# Python API Surfaces

Normative public Python surfaces for embedding Weft. Operation meaning is
owned by the vertical specs; this document owns which names are public, the
stability policy, result/error contracts, and surface layering.

## Public surfaces [PY-1]

| Surface | Import form | Role |
|---|---|---|
| `weft.client` | package (`__all__`) | Primary embedder interface for application logic. |
| `weft.ext` | module (`__all__`) | Extension and downstream contracts. |
| `weft.commands` | package (`__all__`, lazy facade) | CLI-equivalent adapter for process and CLI reuse. |

Each `__all__` is its authoritative public-name inventory. Names not exported
there, including `weft.core.*`, helpers, constants, command leaves, and
`execute_run`, are private.

`weft.ext.__all__` is exactly: `RunnerHandle`, `RunnerCapabilities`,
`RunnerRuntimeDescription`, `AgentResolverResult`, `AgentToolProfileResult`,
`AgentMCPServerDescriptor`, `RunnerEnvironmentProfileResult`, `AgentResolver`,
`AgentToolProfile`, `RunnerEnvironmentProfile`, `TaskRunnerBackend`,
`RunnerPlugin`, and `SpecRunInputRequest`.

`weft.client.__all__` retains its existing inventory and adds exactly
`CommandError`, `CommandUsageError`, `CommandTimeoutError`,
`CommandExecutionError`, `SubmissionError`, `SubmissionValidationError`, and
`SubmissionManagerError`.

## Commands surface contract [PY-2]

Every canonical CLI verb has one actual implementation exported from
`weft.commands`: `cmd_` plus its full CLI path, joined with underscores and
hyphens normalized to underscores. The initial bijection is 41 verbs: 4 root,
14 queue, 6 spec, 6 task, 5 manager, and 6 system. The root callback and
global `--version` are parser features, not verbs. The package resolves exports
lazily and caches them.

Commands accept parsed semantic values, perform semantic validation and
orchestration, return structured outcomes, and raise typed errors. They do not
read process stdin or write process output. `weft.cli` owns shell decoding,
human/JSON formatting, stream routing, and exit translation. Each Typer verb
invokes its corresponding facade export exactly once.

Canonical CLI argument/long-option spellings become parameter names by
replacing hyphens with underscores; callback-local names and short aliases do
not govern them. Repeatable options retain the singular spelling and accept a
`Sequence`; dual flags use the positive semantic spelling (`apply`, `follow`).
Positional arguments are positional-or-keyword and semantic options are
keyword-only. No command accepts `**kwargs`. Presentation-only `--json`,
`--quiet`, `--verbose`, `--error`, and `--timestamps` are excluded. Task-list
`--stats` is also presentation-only; queue-list `--stats` remains semantic.

The exact signature exceptions are: `cmd_run` has `spec_args=()`,
`describe=False`, and `stdin_text=None`;
`cmd_queue_write(queue_name, message=None, *, endpoint=None)` exposes its
positional overload; `cmd_system_task_monitor(..., follow=False)` exposes the
positive side of `--once/--follow`. `cmd_run(describe=True, spec=REF)` returns
spec-aware help metadata and never submits.

### Public command types

`weft.commands.__all__` exports the 41 functions and these consumption types:
`InitResult`, `RunSpecDescription`, `RunSession`, `CommandStream`,
`RunExecutionResult`, `SubmittedTaskReceipt`, `TaskSnapshot`, `TaskResult`,
`TaskEvent`, `ServiceSnapshot`, `TaskPingResult`, `TaskControlResult`,
`TaskControlFailure`,
`QueueEntry`, `QueueInfo`, `QueueWriteReceipt`, `QueueMoveResult`,
`QueueDeleteReceipt`, `QueueBroadcastReceipt`, `QueueAliasRecord`,
`EndpointResolution`, `ManagerSnapshot`, `SpecRecord`,
`SpecValidationResult`, `SpecMutationResult`, `SystemStatusSnapshot`,
`SystemTidyResult`, `SystemLoadResult`, `SystemDumpResult`,
`SystemPruneResult`, `BuiltinSpecRecord`, `TaskMonitorConfig`,
`TaskMonitorResult`, `TaskMonitorRecord`, and `TaskMonitorSummary`.

New/refined exact contracts:

- `InitResult(root: Path, config_path: Path, created: bool)`.
- `RunSpecDescription(reference: str, usage: str,
  arguments: tuple[Mapping[str, Any], ...], stdin: Mapping[str, Any] | None)`.
- `CommandStream[T]` is an iterator with idempotent `close()`; exhaustion and
  close release resources and iteration failures use typed command errors.
- `RunSession` exposes `tid`, `events() -> CommandStream[TaskEvent]`,
  `send_input(text)`, `close_input()`, `stop() -> TaskControlResult`,
  `wait(timeout=None) -> RunExecutionResult`, and idempotent `close()`. Close
  releases owned resources but does not cancel the task.
- `QueueMoveResult(source: str, destination: str,
  entries: tuple[QueueEntry, ...], moved_count: int)`; entries are the exact
  ordered moved set.
- `TaskPingResult(tid: str, acknowledged: bool, timed_out: bool,
  error: str | None, observed_at: int | None, pong: Mapping[str, Any] | None,
  snapshot: TaskSnapshot | None)`.
- `TaskControlFailure(tid: str, error: str, error_type: str)` records a
  selected task whose control attempt could not be confirmed. `error` is the
  rendered failure message and `error_type` is the exception class name.
- `TaskControlResult(command: Literal["stop", "kill"],
  requested: tuple[str, ...], accepted: tuple[str, ...],
  failures: tuple[TaskControlFailure, ...],
  snapshots: tuple[TaskSnapshot, ...])`. `accepted` and `failures` partition
  `requested`. An empty selection is a successful zero-count outcome. In a
  sweep, the command raises only when at least one task was requested and none
  was accepted; the raised `ControlRejected` carries the full failure tuple on
  its documented `failures` attribute. A single genuinely unknown TID instead
  propagates `TaskNotFound`, which the CLI renders with exit code 2. A known
  terminal task rejects `stop` with `ControlRejected` and an `already
  <status>` message before a control queue is written. It remains eligible for
  `kill` so kill escalation can reap runtime residue; when no live runtime can
  be proven, the rejection states the terminal status and absence of runtime
  residue. Client
  `stop_many()`/`kill_many()` with
  no selector are the empty-selection case. Explicit `tids` cannot be combined
  with `all_tasks` or `pattern`; mixed scope is a typed usage error.
- `QueueDeleteReceipt(queue: str | None, deleted_count: int,
  queues_deleted: int, all_queues: bool, exact_message: str | None)`.
- `SpecMutationResult(action: Literal["create", "delete"], record: SpecRecord)`.
- `SystemDumpResult(path: Path, queues: int, messages: int, aliases: int,
  omitted_claimed_queues: int, omitted_claimed_messages: int)`.
- `SystemPruneResult(families: tuple[str, ...], applied: bool,
  candidates: int, deleted: int, failed: int, details: Mapping[str, Any])`.
- `BuiltinSpecRecord(name: str, description: str | None,
  category: str | None, function_target: str | None,
  supported_platforms: tuple[str, ...], path: Path, source: str = "builtin")`.
- `TaskMonitorRecord(record: Mapping[str, Any])` is the lossless
  pre-serialization form of each run-start, task-summary, and run-completed
  record in exact emission order.
- `TaskMonitorResult(log_path: Path | None, records_written: int,
  events_scanned: int, tids_seen: int, summaries_emitted: int,
  checkpoint_timestamp: int | None, records: tuple[TaskMonitorRecord, ...])`.
  It has no exit-code, stdout, stderr, or JSON-rendering fields.
- `TaskMonitorConfig(context: str | Path | None = None, follow: bool = False,
  sink: Literal["stdout", "disk"] = "stdout", log_dir: Path | None = None,
  checkpoint: Path | None = None, no_checkpoint: bool = False,
  since: int | None = None, limit: int | None = None,
  monitor_name: str = "default")` and
  `TaskMonitorSummary(record: Mapping[str, Any])`.
- `RunExecutionResult` has no `submission_error`; terminal status, result, and
  failure detail remain outcome data.
- `SpecRecord` has `payload: Mapping[str, Any] | None = None`; show always
  supplies it. `TaskEvent.payload` losslessly carries lifecycle, output chunk,
  result, and control envelopes in observed order.
- `TaskSnapshot` adds `host_pids`, `managed_pids`, and `live_managed_pids`,
  each `tuple[int, ...] | None = None`; process mode populates them.
- `ManagerSnapshot` adds
  `liveness: Literal["live", "stale", "unknown", "non_live"] | None = None`,
  `proof_source: str | None = None`, `proof_detail: str | None = None`,
  `dispatch_eligible: bool | None = None`,
  `canonical_candidate: bool | None = None`, and
  `canonical: bool | None = None`; diagnostic mode populates them.
  `started_here: bool | None = None` is populated only by
  `cmd_manager_start` so the CLI can preserve its started-versus-existing
  lifecycle message without a second semantic query.

### Deterministic return matrix

| Family | Return |
|---|---|
| root init | `InitResult` |
| root status | `SystemStatusSnapshot`, or `CommandStream[TaskEvent]` iff watch |
| root result | `TaskResult`, tuple in all mode, or `CommandStream[TaskEvent]` iff stream |
| root run | `RunSpecDescription` iff describe; otherwise `RunSession` iff wait; otherwise `RunExecutionResult` |
| queue read/peek/watch | tuple/stream of `QueueEntry` |
| queue write/move/delete/broadcast | corresponding receipt/result |
| queue list/exists/stats/resolve/alias | named `QueueInfo`, `EndpointResolution`, `QueueAliasRecord`, tuple, or `bool` |
| spec create/delete/list/show/validate/generate | mutation, record tuple/record, validation, or mapping as named |
| task list/status/ping/stop/kill/tid | snapshots/events, ping/control result, or full TID |
| manager start/status/list | snapshot or tuple; serve blocks and returns `None` |
| manager stop | terminal snapshot, or `None` when already absent |
| system tidy/load/task-monitor/prune/dump/builtins | corresponding named outcome |

Interactive run only changes session capabilities. Task monitor returns
`CommandStream[TaskMonitorSummary]` iff `follow=True`, else
`TaskMonitorResult`; sink does not alter the branch. `cmd_manager_serve` is the
sole blocking non-stream exception and emits no process output.

### Typed errors and CLI exits

The exact hierarchy is `CommandError(WeftError)`,
`CommandUsageError(CommandError, ValueError)`,
`CommandTimeoutError(CommandError, TimeoutError)`, and
`CommandExecutionError(CommandError, RuntimeError)`. Existing `InvalidTID`,
`TaskNotFound`, `SpecNotFound`, `ControlRejected`, `ManagerNotRunning`, and
`ManagerStartFailed` retain their inheritance. Submission errors are
`SubmissionError(CommandError)`, `SubmissionValidationError(SubmissionError)`,
and `SubmissionManagerError(SubmissionError)`. Backend, validation, and OS
errors are translated with chaining at the command seam. Error classes
exported by client and commands are identical objects.

The CLI maps `CommandUsageError`, `InvalidTID`, `TaskNotFound`, and
`SpecNotFound` to 2; `CommandTimeoutError` to 124; every other `CommandError`
to 1 except the spec-pinned `system load` alias-conflict case, which returns 3
before writes begin; success maps to 0. When an internal remaining-budget or
completion-grace wait expires, the public timeout diagnostic retains the
caller's requested timeout rather than exposing the internal sub-budget.
Ctrl-C remains a shell concern.

## Client submission with declared arguments [PY-3]

`WeftClient.submit_spec(reference, *, spec_args=(), payload=None,
stdin_text=None, **overrides)` and `prepare_spec(...)` share the run pipeline:
parameterization first, remaining tokens to run-input, before TID commit. For
a spec declaring run-input, adapter output is the payload and `payload=` is
rejected. Without run-input, `payload=` is valid. `cmd_run` and client spec
submission accept a single `stdin_text: str | None`. The submission seam routes
it to declared run-input stdin when the spec's `run_input` declares stdin, as
the initial work payload when the spec has no `run_input` contract, and rejects
it with a typed usage error when a `run_input` contract exists but declares no
stdin. Client and command surfaces never read process stdin; the CLI adapter
reads piped stdin once and forwards it as `stdin_text`. Failures are typed; a
returned `Task` is the committed receipt and uses the materialized spec's
runtime context. Runtime roots expand `~` and resolve to an absolute path
through the shared submission seam on every surface. Declared-argument parse
errors are `CommandUsageError`; malformed/materialization and adapter failures
are `SubmissionValidationError` with the original exception chained, except
that an adapter-raised `WeftError` retains its exact public type and mapping. A
non-persistent human-readable `name` is not an endpoint claim and therefore
does not use endpoint-name syntax validation; persistent names still do.
`payload=` and `stdin_text=` are mutually exclusive even when
the spec has no `run_input` contract, so the initial work payload has one owner.

## Layering [PY-4]

Runtime imports are one-way: `cli -> commands -> core`,
`client -> commands -> core`, and `core -> ext`; commands may also import ext.
Commands never import adapters; core never imports commands or adapters; CLI
and client never import each other; runtime ext imports never point back to
core, commands, or adapters. Type-checking-only ext-to-core annotations are
allowed. CLI and core initializers remain import-light markers; client and
commands are public package facades and ext is a public module. Architecture
tests enforce the graph, facade inventory/laziness, CLI bijection, no command
stdin access, and exactly one matching facade invocation per Typer callback.

## Related Plans

- [Python API surfaces plan](../plans/2026-08-11-python-api-surfaces-sb-contract.md)
- [Public API surface remediation plan](../plans/2026-08-12-public-api-surface-remediation.md)
