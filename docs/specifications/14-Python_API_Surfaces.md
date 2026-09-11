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
`CommandExecutionError`, `SubmissionError`, `SubmissionValidationError`,
`SubmissionManagerError`, and `normalize_taskspec_payload`.

## Commands surface contract [PY-2]

Task observations use the shared per-TID runtime-state readers
([OBS.6]); known-TID snapshots read that state queue directly. Snapshot and
control waiters subscribe to the exact queue even before its first write;
realtime snapshot requests remain fresh. CLI event watches remain incremental
event streams. This storage change adds no public signature, result shape,
iterator retention contract, or lifecycle precedence change. Python system
status retains its unfiltered default; CLI status retains terminal filtering.

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
- `TaskResult` retains its result fields and carries optional
  `reconciliation: dict[str, Any] | None = None` so the CLI can render the
  existing claimed-result metadata contract [CLI-1.2.2]. The owner is
  `weft/commands/result.py::await_task_result`; the CLI renders that evidence
  without another lifecycle probe. Implementation plan:
  [Dead generation retirement](../plans/2026-08-31-dead-generation-retirement-plan.md).
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

Client system dump, load, and tidy preserve the supplied resolved
context, including its broker target and artifact directory. Without an
explicit output, the export is `weft_export.jsonl` in that context's
Weft directory; without an explicit input, load reads that same file.
A `RunSession.stop` likewise controls the task on the context the
session was submitted with.

Implementation: `weft/commands/dump.py::dump_system` and `cmd_system_dump`
share the resolved-context materializer; `weft/commands/load.py` and
`weft/commands/tidy.py` share theirs the same way, and
`weft/commands/run.py::_LiveRunSession.stop` passes its session context
to the task-control owner.
Correction plan: [Complexity review corrections](../plans/2026-09-08-complexity-review-corrections-plan.md).

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

Submit-override semantics are identical on every surface that accepts
`**overrides`: an override whose value is `None` is ignored and the template's
value stands; a name outside the shared public override vocabulary raises
`TypeError` from `prepare(...)`, `prepare_pipeline(...)`, and
`submit_command(...)`, and `SubmissionValidationError` from `prepare_spec(...)`;
a value the TaskSpec schema rejects raises the schema's validation error (a
`ValueError`) from `prepare(...)` and `SubmissionValidationError` from
`prepare_spec(...)`; a `name` in the reserved `_weft.` namespace raises
`ValueError`. Each `submit(...)`, `submit_spec(...)`, and `submit_pipeline(...)`
raises exactly what its `prepare*` counterpart raises.
`weft.client.normalize_taskspec_payload(taskspec, **overrides)` runs that same
contract without a client or context and returns the validated, normalized
TaskSpec definition as a fresh JSON-compatible `dict`: the pre-transport
snapshot `prepare(...)` would hold for the same inputs — overrides applied,
re-validated, JSON round-tripped — before any submission-time transport
encoding or reserved-metadata handling. It accepts a `TaskSpec` or a
JSON-compatible mapping (a mapping without `tid` is validated as a template),
raises what `prepare(...)` raises, constructs no Weft context, reads no
configuration, resolves no project root, opens no broker, and writes nothing.
Every keyword argument is an override name: `payload` is not part of the
override vocabulary and raises `TypeError` here. The mapping carries no
top-level bundle-root marker and no bundle provenance; a `TaskSpec` input that
already carries a bundle root is accepted, its root is re-resolved read-only by
TaskSpec validation, and the result drops it. Embedders that need a TaskSpec
definition for composition rather than submission call it; there is no second
normalization path.

Implementation: `weft/client/_client.py::normalize_taskspec_payload` over
`weft/commands/submission.py::prepare_definition`.

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

- [Per-TID task-state namespace](../plans/2026-09-11-per-tid-task-state-namespace-plan.md)

- [Python API surfaces plan](../plans/2026-08-11-python-api-surfaces-sb-contract.md)
- [Public API surface remediation plan](../plans/2026-08-12-public-api-surface-remediation.md)
- [Django override normalization seam plan](../plans/2026-09-08-django-override-normalization-seam-plan.md)
