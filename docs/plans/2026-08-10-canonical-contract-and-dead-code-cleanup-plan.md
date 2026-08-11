# Canonical Contract and Dead Code Cleanup Plan

Status: completed
Source specs: `docs/specifications/00-Overview_and_Architecture.md` Runtime Layers; `docs/specifications/00-Quick_Reference.md`; `docs/specifications/01-Core_Components.md` [CC-2.4], [CC-3.1], [CC-3.2], [CC-3.4]; `docs/specifications/02-TaskSpec.md` [TS-0], [TS-1], [TS-1.3]; `docs/specifications/03-Manager_Architecture.md` [MA-1], [MA-1.4], [MA-1.6], [MA-3]; `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.1], [SB-0.4], [SB-0.4a], Current Context API; `docs/specifications/05-Message_Flow_and_State.md` [MF-3], [MF-5], [MF-7]; `docs/specifications/06-Resource_Management.md` [RM-1] through [RM-5.1]; `docs/specifications/07-System_Invariants.md` [EXEC.3], [OBS.13.8], [OBS.13.9], [OBS.16], [OBS.17], [MANAGER.3], [MANAGER.8]; `docs/specifications/08-Testing_Strategy.md` [TS-3.1]; `docs/specifications/09-Implementation_Plan.md` [IP-1.0], [IP-1.1], [IP-2.1]; `docs/specifications/10-CLI_Interface.md` [CLI-0.2], [CLI-0.3], [CLI-1.1.1], [CLI-1.2], [CLI-5], [CLI-6]; `docs/specifications/11-CLI_Architecture_Crosswalk.md` [CLI-X1], [CLI-X2], [CLI-X3]; `docs/specifications/12-Pipeline_Composition_and_UX.md` [PL-0], [PL-1.2]; `docs/specifications/13-Agent_Runtime.md` [AR-0.1], [AR-2.1], [AR-2.2], [AR-9]
Superseded by: none

Class: 5. This removes public import and CLI paths, changes TaskSpec
validation, replaces the control wire format, narrows the manager authority
proof and service-owner schema, migrates Monitor-owned persisted data, and
changes external JSONL record types. Those are public, execution, and
persistence contract changes with one-way rollout edges.

Plan type: implementation with spec revision.

## 1. Goal

Give each current Weft behavior one canonical owner and one canonical path.
Delete compatibility facades, old dependency branches, inert parameters,
test-only production code, redundant modules, and old-release-only repair
lanes. Consolidate exact and near-duplicate logic only where one owner can
preserve the current semantics without adding a generic framework.

This is a cleanup, not a product-improvement project. Except for the explicitly
listed compatibility contracts, accepted formats, and no-op surfaces, preserve
the current tests, invariants, exit classes, queue policy, state semantics, and
runtime behavior.

## 2. Source Documents and Required Reading

Read before editing:

1. `AGENTS.md` and the complete `docs/agent-context/` read order.
2. The source specs and section codes in this plan's metadata.
3. `docs/agent-context/runbooks/writing-plans.md`,
   `hardening-plans.md`, `review-loops-and-agent-bootstrap.md`,
   `testing-patterns.md`, `runtime-and-context-patterns.md`, and
   `adversarial-acceptance-probes.md`.
4. `docs/agent-context/lessons.md` and `docs/lessons.md`.
5. The implementation and tests named in the inventory and task being changed.
6. `README.md` context-discovery behavior and the current `AGENTS.md` package
   layout, both of which must be synchronized without overwriting unrelated
   user edits.

Historical plans are context only. They may explain why a compatibility path
was added, but they do not override the current specs or the cleanup policy
approved for this plan. Do not edit old plans to make their old paths look
current.

Before implementation, the owner must be able to answer:

- Which fields are durable service-registry data, and which similarly named
  fields are only a command/status projection?
- Which Monitor repair states can a current writer or interrupted current
  transaction still create?
- Which control producer writes each control command, and how will every
  producer move in the same slice?
- Which foreground TaskMonitor counts differ from supervised Monitor counts on
  malformed or missing-TID input?
- Which resource-monitor method family is actually called by runners?
- Which runner outcomes can complete without a `runtime_handle`, if any?

## 3. Spec Baseline

- `3bc9bbd1e9e67fcd91716dfccda3a9efd13115f2` is the committed baseline for
  all source specs at plan authoring time.
- `docs/specifications/08-Testing_Strategy.md` and its planned companion had
  pre-existing user worktree edits at plan authoring time. Those edits are not
  part of this plan. Re-read and merge around them if the testing mapping must
  change; do not overwrite them.
- Promotion strategy: **A, in-file requirement text before link claims**.
  Promote the exact behavior text below after plan review and before production
  edits. Add implementation mappings and reciprocal code/test links only as
  their owning implementation slices land.
- Promotion baseline identifier: committed baseline
  `3bc9bbd1e9e67fcd91716dfccda3a9efd13115f2` plus the reviewed uncommitted
  13-spec promotion diff recorded in the Execution Log.

## 4. Scope Rules

Use these rules to prevent this cleanup from becoming a redesign:

1. **One contract, one owner.** A public behavior may have adapters, but only
   one module owns its normalization, reduction, schema, or policy.
2. **Adapters are allowed; compatibility facades are not.** A CLI adapter that
   parses or renders real behavior stays. A module that only preserves an old
   import path or renames another function goes.
3. **One boundary normalizer is not a second runtime path.** Keep the current
   SimpleBroker exact-message-ID input boundary that accepts a Python integer
   or canonical external string and immediately normalizes to `int`. It is a
   current typed boundary, not an old-version subsystem. Do not broaden this
   exception to persisted Monitor data, service records, control envelopes, or
   TaskSpec extras.
4. **Current crash repair stays.** Recovery for interrupted current writes,
   pre-checkpoint gaps, retryable exact deletion, forced-process residue,
   orphan current rows, and `result_without_terminal` remains. Delete only a
   lane whose only producer is an older Weft release or a removed schema.
5. **Capability branches stay.** POSIX/Windows behavior, native-waiter versus
   polling fallback, and runtime-specific metric adapters are current platform
   paths, not backward compatibility.
6. **Do not merge unlike policy.** Runtime-state pruning and retention pruning
   keep separate selection/apply policy. Foreground TaskMonitor output and
   supervised durable Monitor policy stay separate. Persisted service-owner
   rows and public status projections stay separate.
7. **Do not add a framework.** Share a helper only when two current owners
   would otherwise repeat the same rule. A one-line local expression is better
   than a new “common” module with no domain owner.
8. **Tests do not grant production ownership.** Code called only by tests fails
   the deletion test unless the test is the only practical proof of a real
   public contract.

## 5. Invariants and Constraints

The cleanup must preserve:

- 64-bit SimpleBroker hybrid TIDs, their string form, and immutability.
- forward-only task state transitions and the current terminal-state set.
- reserved-queue keep/requeue/clear policy and exact-message deletion.
- `spec` and `io` immutability after TaskSpec creation.
- spawn-based isolation for broker-connected processes.
- queue-first lifecycle/result truth. Monitor tables remain derived operational
  state.
- exclusion of `weft.state.*` queues from dump/load.
- current result, stop, kill, timeout, stream, pipeline, manager-drain,
  autostart, resource-limit, and extension-runner behavior unless this plan
  names the exact compatibility contract being removed.
- current human output and exit classes except for removed options or now
  invalid input.
- no new dependency, no new execution path, no second state store, and no
  public plugin framework.

Fatal versus best-effort boundaries:

- malformed existing project config is fatal and must not be rewritten.
- atomic replace failure is fatal to that write and must leave the old target
  intact; the caller may suppress it only where the current caller contract is
  explicitly advisory.
- a resource-monitor constructor `TypeError` is fatal and is not retried as an
  alternate signature.
- failure to obtain optional runner PONG detail remains best-effort and must not
  suppress the core PONG.
- a missing or malformed manager PONG is absence of authority proof, not proof
  of death.

## 6. Canonical Ownership Inventory

### 6.1 Public paths and shallow compatibility modules

| ID | Current paths | Canonical owner | Planned disposition |
|---|---|---|---|
| P1 | `weft/cli/bootstrap.py`, `weft/cli/__main__.py`, and lazy exports in `weft/cli/__init__.py` | `weft/bootstrap.py`, `weft/__main__.py`, and the `weft.cli.app` leaf module | Delete the bootstrap re-export and alternate module entry. Reduce the CLI initializer to a package marker. Keep installed `weft` and `python -m weft`. |
| P2 | lazy exports in `weft/__init__.py`, `weft/commands/__init__.py`, `weft/core/__init__.py` | `weft.client` for the public client; leaf modules internally | Root retains only real package metadata. Commands/core initializers become package markers. Remove facade identity tests and suppression rows. |
| P3 | `weft/commands/status.py` and `system.collect_status` | `weft/commands/system.py` | Delete both compatibility aliases. |
| P4 | `weft/commands/task_evidence.py` | `weft/core/task_evidence.py` | Delete the command facade and import the core owner directly. |
| P5 | seven-line `builtins.py`, `dump.py`, `load.py`, `tidy.py` wrappers over private support modules | the named public command modules | Move each implementation into its public owner; delete the private support file. |
| P6 | `weft/commands/diagnostics.py` | `weft/commands/tasks.py::format_runner_diagnostics` delegates to `weft/core/runner_diagnostics.py::diagnostic_summary` | Move the small user-facing formatter into the task command owner, retarget the CLI and tests, and delete the one-line module. |
| P7 | class re-exports in `core/agents/backends/__init__.py` | leaf backend modules | Retain backend registration only. |
| P8 | unused export in `core/pruning/__init__.py` | leaf pruning modules | Reduce initializer to a package marker/docstring. |
| P9 | root exports from first-party runner extension packages | each extension's `plugin.py` | Tests and consumers import the declared leaf entrypoint. Keep extension registration behavior. |
| P10 | `client/_errors.py` | `client/__init__.py` importing the real exception owner | Delete the pure re-export file. |
| P11 | `manager_runtime` private implementations/classes plus 16 public one-line or identity twins | public names in `weft/core/manager_runtime.py` | Rename every real implementation and dataclass to its public name and delete all delegating functions and class assignments. |
| P12 | private aliases in `weft/commands/manager.py` and dead `commands.submission.generate_tid` | the public `manager_runtime` functions | Call the canonical module surface directly; patch it directly in tests; delete the unused submission wrapper. |

Legitimate compact facades that remain: `weft.client`,
`weft.core.taskspec`, `weft.core.tasks`, and `weft.core.runners`. The runner
facade is used by separately packaged first-party extensions. Also retain
`client/_prepared.py`, exception modules, and process entry modules because
each owns a real type, taxonomy, or process boundary. Move
`CleanupPolicyRun` from the one-dataclass
`core/monitor/policies/types.py` module into its sole consumer,
`core/monitor/policies/task_log.py`, and delete the shallow module.

### 6.2 Duplicate logic and misplaced ownership

| ID | Duplicate or misplaced code | Canonical owner | Boundary to preserve |
|---|---|---|---|
| D1 | Foreground `commands/task_monitor.py` repeats state timestamp, classification, and name parsing from `core/monitor/runtime.py`; its reducer is only a near-copy | `core/task_evidence.py` owns the three proven generic parsing rules | Foreground and supervised result types/reducers remain separate unless characterization proves full equivalence for malformed rows, missing TIDs, row preservation, limits, and counts. The command retains checkpoint, terminal-only summary, output, and rendering. |
| D2 | runtime and retention prune copies of age, run-ID, limit, and status helpers | existing `core/queue_window.py` owns age/old-enough and `core/task_evidence.py` owns status parsing; inline the current timestamp/nanoseconds/PID run-ID expression and slicing in each policy | The two candidate selectors, proof rules, rescan/apply behavior, retention archive-before-delete, and trivial result/config types remain separate. Do not create a “common” helper for one expression or change the run-ID format. |
| D3 | two prune command facades while family dispatch is repeated in the CLI and rendering/report concerns live in core | one `weft/commands/prune.py` command owner | Core runtime and retention modules retain their separate typed configs/results, selection, rescan, exact apply, and retention archive safety. Because mandatory retention archives and optional reports share record encoding under the archive lock, core also retains that narrow encoder/writer. The command owner performs family dispatch, context selection, optional report orchestration, rendering, and exit classification without merging the two policies. |
| D4 | `ResourceMonitor`/`PsutilResourceMonitor` aliases and long/short method bridges | one real `weft.core.resource_monitor.ResourceMonitor` implementing `start`, `stop`, `snapshot`, `check_limits`, `last_metrics` | Preserve metrics, process-tree scope, 4-of-5 CPU rule, single-sample other limits, and idempotent stop. |
| D5 | manager autostart maps mirror `ManagedServiceState` | `weft/core/manager_services.py::ManagedServiceState` | Preserve `once`/`ensure`, scan throttle, backoff, restart limits, invalid manifests, and removal semantics. A non-mirroring source identity index may remain if needed. |
| D6 | service registry writes identity and queue fields twice and reads every form | `weft/core/service_convergence.py` builds/parses the exact persisted shape | Public manager/status snapshots may project top-level convenience fields from the canonical row. |
| D7 | production PID wrappers in manager, manager runtime, and system | imported `helpers.pid_is_live` binding | Keep the test harness instance adapter used for isolated process tests. |
| D8 | `commands/run.py` test-only wrappers and `cmd_run` renderer | structured `execute_run`/`_execute_*` plus `cli/run.py` | Preserve execution cleanup, timeout, manager wait, pipeline behavior, and renderer exit codes. |
| D9 | fake Typer compatibility object in `commands/_streaming.py` | the real injected/text emit boundary | Preserve streaming order and error behavior. |

Do not introduce a generic prune result base, merge service-record storage with
status projections, merge foreground output policy into the durable monitor, or
unify TTL expiry evidence with report age arithmetic without proof. Those
near-copies have different contracts.

### 6.3 Strict contract removals

| ID | Compatibility path | New single contract |
|---|---|---|
| C1 | raw and JSON `ctrl_in` commands | JSON object `{"command": "...", "request_id": "..."}`, with `request_id` optional. One core encoder/parser is used by every producer and `BaseTask`. |
| C2 | manager PONG selection fields optional | manager authority proof requires role, canonical requests queue, ctrl queues, outbox, context, nonterminal status, and `should_stop != true`. |
| C3 | persisted service rows duplicate `owner_tid/tid`, nested/top-level queues, and `requests/inbox` | one versioned nested schema built and parsed by service convergence. |
| C4 | `provider_cli` missing authority defaults to general | `provider_cli` requires explicit `authority_class`. `llm` may keep its safe bounded default. |
| C5 | `approval_required: false` is accepted but inert | remove the field; both boolean values are unknown-field errors. |
| C6 | top-level TaskSpec `extra="allow"` and private bundle-root extra | reject public unknown top-level keys; `weft/core/taskspec/transport.py` is the sole owner of the reserved internal `_weft_bundle_root` transport key and removes it before strict TaskSpec validation. |
| C7 | resource-monitor old methods, class alias, discarded constructor args, retry-after-any-`TypeError` | one class, one method family, one documented keyword constructor shape, one constructor call. |
| C8 | SimpleBroker pre-v7 iterator signatures | call the supported v7 API only. Internal `TypeError` propagates. |
| C9 | psutil `connections()` fallback | psutil 7 `net_connections()` only. |
| C10 | Microsandbox callable/awaitable sandbox name | current dependency's `.name` attribute only. |
| C11 | upward discovery of legacy SQLite projects | auto-discover only Weft-scoped broker config; otherwise resolve the current explicit root. |
| C12 | `system prune` implicit runtime-state family | require explicit `--family`. Keep dry-run as the default action. |
| C13 | hidden `weft run --monitor` option that only rejects | remove the option and command parameter. Unknown-option handling is sufficient. |
| C14 | old external `record_type=task_log_collated` | emit the already canonical `task_summary` or `service_summary` classification. |
| C15 | Monitor normal readers accept old JSON ID representation and old child tombstones | one bounded schema migration, then strict current-format readers and current-writer repair only. |
| C16 | `WEFT_LOGGING_ENABLED` has a private compatibility truth table | use the same current boolean normalization contract as other Weft boolean configuration and document it once. |
| C17 | deprecated `bin/release.py --publish` and `bin/ruff_suppression_index.py --spec` | canonical script invocations only. |

### 6.4 Dead code and inert parameters

Delete with their compatibility-only tests:

- `weft/cli/_argv.py` and `tests/cli/test_rearrange_args.py`.
- `tests/fixtures/taskspecs.py` after its sole consumer imports
  `tests.taskspec.fixtures` directly.
- `weft/shell/`, its interpreter constants in `weft/_constants.py`, and
  `tests/shell/`. Do not copy its unused interactive flags into the live
  subprocess runner.
- `TaskSpec.get_default_queues()` and `TaskSpec.apply_defaults()`.
- `MultiQueueWatcher.check_interval`, `_check_counter`, callers, benchmark
  arguments, and snapshot-ledger names.
- `Manager.handle_termination_signal` only. Keep the real BaseTask method and
  use Manager's `note_termination_signal` owner handoff.
- `commands.run._run_inline`, `_run_spec_via_manager`, `_run_pipeline`, and
  `cmd_run`.
- `commands._streaming._TyperCompat`.
- `context.get_context`.
- manager `metadata.legacy_role` and the unused
  `manager_event_autostart_source` branch.
- `commands.system.collect_status`.
- Python-below-3.10 import logic in `weft/_runner_plugins.py`.
- `RunnerOutcome.worker_pid` only after proving every completed outcome that
  needs identity has a `runtime_handle` and preserving live
  `on_worker_started` callbacks.
- ignored `commands.tasks.include_process` and the matching client parameters;
  retain the real CLI `task status --process` projection if it still augments
  output.
- ignored `manager_runtime.verbose`; presentation remains in command/CLI code.
- ignored `commands.submission.prepare_taskspec(context)` argument; do not
  remove context from preparation paths that actually use it.

## 7. Proposed Spec Delta

The text below is the review target. The committed baseline remains governing
until this text is promoted.

Promotion strategy:

| Spec file | Strategy | Sections |
|---|---|---|
| `00-Overview_and_Architecture.md` | A | Runtime Layers |
| `01-Core_Components.md` | A | [CC-2.4], [CC-3.4] |
| `02-TaskSpec.md` | A | [TS-0], [TS-1] |
| `03-Manager_Architecture.md` | A | [MA-1], [MA-1.4], [MA-1.6], [MA-3] |
| `04-SimpleBroker_Integration.md` | A | [SB-0.4], [SB-0.4a], Current Context API |
| `05-Message_Flow_and_State.md` | A | [MF-3], [MF-5], [MF-7] |
| `06-Resource_Management.md` | A | [RM-5], [RM-5.1] |
| `07-System_Invariants.md` | A | [EXEC.3], [OBS.13.8], [OBS.13.9], [MANAGER.3], [MANAGER.8] |
| `09-Implementation_Plan.md` | A | [IP-1.0], [IP-2.1] |
| `10-CLI_Interface.md` | A | [CLI-0.2], [CLI-1.1.1], [CLI-5], [CLI-6] |
| `11-CLI_Architecture_Crosswalk.md` | A | [CLI-X1], [CLI-X2], [CLI-X3] |
| `12-Pipeline_Composition_and_UX.md` | A | [PL-0], [PL-1.2] |
| `13-Agent_Runtime.md` | A | [AR-0.1], [AR-2.1], [AR-2.2], [AR-9] |

Mapping-only path changes in these sections are applied with the owning code
slice. Requirement text is promoted first without premature implementation
claims.

Non-normative synchronization in the owning implementation slices:
`README.md` for context discovery and `AGENTS.md` for removal of
`weft/shell/`.

### 7.1 Runtime Layers: insert after the layer mapping

> **Single current contract.** Weft does not preserve alternate import paths,
> CLI spellings, control envelopes, persisted record shapes, dependency APIs,
> or accepted schema fields solely for backward compatibility. A contract
> change updates all Weft-owned producers and consumers in one coordinated
> release. Durable old data is handled by a bounded forward migration; normal
> readers and writers use only the current shape after that migration.
> Multiple documented forms may meet at one boundary normalizer only when all
> forms are part of the current contract and immediately become one internal
> representation.
>
> The public Python adapter is `weft.client`. Internal and command code imports
> its owning leaf modules directly. The `weft` package root exposes package
> metadata only; `weft.commands` and `weft.core` do not preserve symbol
> inventories from older module layouts.

Replace the `09-Implementation_Plan.md` [IP-1.0] facade paragraph with:

> The public `weft.client` package is the Python adapter. The `weft` package
> root contains package metadata, while `weft.commands` and `weft.core` are
> package namespaces rather than compatibility export surfaces. Production
> code imports the leaf module that owns the behavior.

Replace the `weft/shell/` row in [IP-1.0] with:

> `weft/core/runners/` and runner plugins own process/session launch details
> and runtime handles. There is no separate shell command-rewrite layer.

### 7.2 TaskSpec strictness: insert in [TS-1]

> Every public TaskSpec model rejects unknown fields, including the top-level
> object. `weft/core/taskspec/transport.py` is the sole owner of bundle-root
> provenance. `decode_taskspec_transport_payload()` copies a transport mapping,
> removes and validates the reserved internal `_weft_bundle_root` key, strictly
> validates the remaining TaskSpec, and stores the root only in the TaskSpec
> private attribute. `encode_taskspec_transport_payload()` reattaches that key
> only when serializing a TaskSpec to a spawn queue or process boundary.
> `validate_taskspec_payload(..., bundle_root=...)` handles stored-spec and
> bundle callers that already know the root without first inserting an extra
> key. Template and resolved validation modes remain explicit so implicit
> spawn requests may acquire their committed message-ID TID at the existing
> manager boundary. Runner-plugin validation receives bundle provenance through
> an explicit `bundle_root` keyword because its validation mapping is only a
> partial TaskSpec shape; that mapping never carries `_weft_bundle_root` and is
> not decoded as a full TaskSpec. The private value is absent from ordinary
> `model_dump()` output.

### 7.3 Control protocol: replace the first two [MF-3] bullets

> - `ctrl_in` accepts one structured JSON object shape. The required
>   `command` field contains a supported uppercase control command. An optional
>   `request_id` correlates keyed replies. Raw command strings are not a
>   supported control request.
> - every Weft-owned controller uses the shared control-envelope encoder.
>   `BaseTask` uses the matching parser and rejects malformed, non-object, or
>   unsupported envelopes without treating them as commands.
> - the envelope contains exactly `command` and, when present, `request_id`.
>   Commands use one of the six exact uppercase values `PING`, `STATUS`,
>   `STOP`, `KILL`, `PAUSE`, or `RESUME`; a present request ID is a string
>   containing at least one non-whitespace character. Extra keys, alternate
>   case, blank identifiers, and scalar JSON are
>   invalid. Replies to keyed requests echo the request ID.
> - `ctrl_out` carries task-local replies and terminal notifications. Readers
>   ignore malformed or unrelated replies as protocol noise; this robustness
>   does not make removed reply formats current contracts.

Use the same requirement text in [CC-2.4]. The canonical encoder and parser are
`weft/core/control_messages.py`; this module owns wire shape only, not task or
manager control policy. BaseTask exact-acknowledges an invalid row without
dispatch or reply so it cannot block later controls. Every reply to a valid
keyed request, including PAUSE and RESUME, echoes the request ID.

Replace the manager PONG field bullet in [MF-3] with:

> - a manager PONG contains `role="manager"`, `requests`,
>   `ctrl_in`, `ctrl_out`, `outbox`, `weft_context`,
>   `task_status`, and `should_stop`. A matched PONG proves manager selection
>   authority only when every field is present with its exact type, queue and
>   context values match the candidate record and resolved context,
>   `task_status` is `created`, `spawning`, or `running`, and
>   `should_stop is False`. Missing, malformed, mismatched, draining, stopping,
>   or terminal PONG data is absence
>   of authority proof, not proof that the manager is dead.

PONG matching uses the exact canonical response fields `command="PING"`,
`status="ok"`, and `message="PONG"` without case or whitespace normalization.
A draining manager publishes `should_stop=true` in its snapshot while its
drain loop remains active. The shared eligibility gate owns the outbox check;
manager-runtime does not add a second narrowing rule.

Remove [MF-3] text that grants raw STOP/KILL special handling or protects
“legacy” reply payloads. Retain the current keyed-reply ownership and cleanup
rules for the one JSON protocol.

### 7.4 Service-owner record: insert in [MA-1.4] and [MANAGER.3]

> `weft.state.services` stores one `schema="weft.service_owner.v2"`
> service-owner object. Required keys are `schema`, `service_key`,
> `service_type`, `owner_tid`,
> `name`, `status`, `queues`, `runtime_handle`, and `metadata`.
> Manager rows also carry `role` and `capabilities`. `queues` is the only
> persisted queue-name mapping. The persisted object does not duplicate
> `owner_tid` as `tid`, does not copy queue names to top-level fields, does
> not alias `requests` as `inbox`, and does not carry `legacy_role`.
> Manager/status read models may project documented convenience fields from
> this canonical object, but they are not alternate persisted schemas.
>
> Before an existing `weft.state.services` queue is passed to manager,
> status, or Monitor logic, the shared
> `discard_v1_service_registry_rows(queue)` bootstrap helper scans it and
> exact-deletes only rows whose schema discriminator is
> `weft.service_owner.v1`. It does not parse or transform those bodies and
> never republishes them with a fresh timestamp. A verification scan must find
> no v1 row before bootstrap returns. Existing v2 rows and their message IDs
> are untouched. An unknown future schema fails bootstrap. Normal service-owner
> parsing accepts v2 only. Running managers and services rebuild discarded
> state by publishing their own current v2 heartbeats.

Add to [MA-1.6]:

> `ManagedServiceState` is the sole mutable owner of built-in and autostart
> service launch, restart, deadline, and backoff state. Autostart source
> discovery may retain a source identity index, but it must not mirror those
> lifecycle values in parallel maps.

Replace the Manager signal compatibility clause in [MA-3] with:

> Signal handlers record requests through `note_termination_signal`. The
> manager reactor applies them through its owner-thread transition path. There
> is no Manager-specific synchronous signal alias.

### 7.5 Resource monitor: replace [RM-5.1] opening and interface text

> The default and only built-in psutil monitor class is
> `weft.core.resource_monitor.ResourceMonitor`. The monitor protocol is
> `start()`, `stop()`, `snapshot()`, `check_limits()`, and
> `last_metrics`. Custom monitor classes use that protocol and one documented
> keyword constructor accepting the current limits and polling interval.
> The loader invokes that constructor once. Constructor `TypeError` and other
> implementation errors propagate; they are not reinterpreted as evidence for
> an older signature.
>
> The built-in monitor imports the required psutil dependency directly and
> uses its supported `net_connections()` API. Platform-specific file
> descriptor, handle, and process-disappearance behavior remains part of the
> current monitor implementation.

### 7.6 Context, config, and Monitor migration: insert in [SB-0.4]

> Automatic Weft context discovery searches upward only for the configured
> Weft-scoped broker configuration (by default `.weft/broker.toml`). If none
> is found, Weft resolves the current explicit root. Weft does not search
> parent directories for an old SQLite database filename.
>
> A missing `.weft/config.json` may be created with current defaults. An
> existing unreadable file, malformed JSON document, or non-object document is
> an error. Weft reports the error and does not replace or modify the file.
> Weft-owned metadata writes described as atomic use a same-directory temporary
> file and atomic replacement. Failure to publish the replacement propagates
> and leaves any prior target bytes unchanged. A caller may suppress that error
> only when its own documented output is advisory.

Replace the corresponding Quick Reference configuration rows with:

> | `WEFT_DEFAULT_DB_LOCATION` | Default SQLite broker location used for current explicit-root resolution. |
> | `WEFT_DEFAULT_DB_NAME` | Default SQLite broker filename used for current explicit-root resolution. It is not an upward-discovery marker. |
> | `WEFT_PROJECT_SCOPE` | Whether Weft searches upward for its configured Weft-scoped broker configuration. |
> | `WEFT_BACKEND`, `WEFT_BACKEND_TARGET`, `WEFT_BACKEND_HOST`, `WEFT_BACKEND_PORT`, `WEFT_BACKEND_USER`, `WEFT_BACKEND_PASSWORD`, `WEFT_BACKEND_DATABASE`, `WEFT_BACKEND_SCHEMA` | Environment-selected broker backend and connection details. A discovered Weft-scoped broker configuration wins; otherwise these values apply to current explicit-root resolution. |

Replace the README discovery paragraph with:

> When Weft auto-discovers a project from a child directory, upward search
> considers only the configured Weft-scoped broker configuration (by default
> `.weft/broker.toml`). A discovered file is authoritative over ambient backend
> selection. If no such file is found, Weft resolves the current directory as
> the explicit root and applies current environment backend selection there.
> An ancestor SQLite database filename and a root `.broker.toml` do not claim
> the child directory.

Replace Current Context API references to `get_context` with:

> `build_context()` is the single context-construction entry point.

Append to [SB-0.4a]:

> Monitor schema version 6 has one migration edge. A newly created store writes
> version 6. Existing Monitor tables with no version metadata may be initialized
> as version 6 only when every Monitor-owned table is empty; non-empty
> unversioned stores fail. Version 5 migrates transactionally to version 6.
> Version 6 is verified without migration. Versions below 5 and above 6 fail as
> unsupported. There is no generic “lower than current” version advance.
>
> The version 5 to 6 migration rewrites only the explicitly owned JSON
> message-ID fields, advances queued deferred external envelopes from external
> schema version 1 to 2 without traversing opaque payload fields, physically
> removes obsolete child-message tombstones, and resets the old
> parent-raw-deleted with surviving-child-ref state to ordinary pending cleanup.
> That parent state is not reachable from the current writer and has no normal
> repair lane after migration. The migration advances the schema version only
> after every rewrite succeeds.
> Ordinary current-version readers accept only the canonical stored form.
> Startup fails on malformed owned data rather than installing a permanent
> tolerant reader. Repair for states that current writers can create during an
> interrupted transaction remains bounded and idempotent.

### 7.7 Observation record types: replace the compatibility paragraph in [MF-5]

> External collated JSONL uses the same classification as the reduced summary:
> task rows emit `record_type="task_summary"` and manager or managed-service
> rows emit `record_type="service_summary"`. The external writer does not
> rename both classes to `task_log_collated` or add a redundant compatibility
> discriminator. This record-shape change advances the shared external task-log
> `schema_version` from 1 to 2; all external task-summary, service-summary, raw,
> and lifetime-report records use version 2 after the cutover.

Replace old-release repair text in [MF-5], [OBS.13.9], and [SB-0.4a] with:

> Monitor repair handles only states reachable from the current writer and
> current cleanup transactions, including interrupted exact deletion,
> pre-checkpoint gaps, pending deferred writes, and forced-process residue.
> Data written by an older Monitor schema is handled by its schema migration,
> not by a permanent normal-cycle compatibility lane.

### 7.8 CLI contract: insert in [CLI-0.2] and replace affected command text

> Supported process entry points are the installed `weft` command and
> `python -m weft`. Both enter through `weft/bootstrap.py` so
> `WEFT_ENV_FILE` is processed before normal configuration import. The
> `weft.cli` package is not a separate executable entry point.

Remove `run --monitor` from [CLI-1.1.1] and add:

> `weft run` has no `--monitor` option. Monitoring is configured in a stored
> TaskSpec and is not represented by a rejected compatibility flag.

Replace the [CLI-6] prune-default sentence with:

> `weft system prune` requires at least one explicit `--family` value and
> remains dry-run unless `--apply` is supplied. There is no implicit family.

Replace the corresponding Quick Reference note with:

> `weft system prune` requires an explicit family and defaults to dry-run.
> Runtime-state, retention, and `all` family behavior retain their current
> deletion and archive protections.

Update [CLI-5] discovery and bootstrap mappings to the [SB-0.4] rule and the
single `weft/bootstrap.py` owner.

In `12-Pipeline_Composition_and_UX.md`, change status ownership to
`weft/commands/system.py` and remove `--monitor` from the pipeline-specific
incompatible-option list because it is no longer a parsed `weft run` option.

### 7.9 Agent schema: replace authority and approval text in [AR-0.1],
[AR-2.1], and [AR-2.2]

> - `llm` resolves an omitted `authority_class` to the safe value
>   `bounded`.
> - `provider_cli` requires an explicit `authority_class` of `bounded` or
>   `general`, subject to the selected provider's supported authority.

Replace the `approval_required` paragraph with:

> Agent tool descriptors do not contain `approval_required`. Weft does not own
> interactive tool approval policy at this boundary. Either boolean spelling
> is an unknown field and validation fails with the same strict-schema error as
> any other unsupported tool field.

### 7.10 Configuration boolean normalization: add to the Quick Reference

> Boolean `WEFT_*` settings use one documented normalizer. Unset, empty,
> `0`, `f`, `false`, `none`, and `null` values are false, with matching
> case ignored; any other non-empty string is true. Explicit boolean overrides
> remain booleans, and explicit string overrides use the same string parser.
> `WEFT_LOGGING_ENABLED` does not use a private compatibility truth table.

## 8. Implementation Slices

Each slice is independently reviewable. Do not batch all deletions into one
diff.

### Spec-promotion slice: make the new contract normative

Files:

- every spec in the promotion table whose exact text changes
- `docs/specifications/00-Quick_Reference.md`
- this plan's reciprocal backlinks in each touched spec

Actions:

1. Before any spec or production edit, capture and record the `before`
   backstitch report using the first command in `12`.
2. Apply `7` text with local wording adjusted only to fit the exact heading and
   grammar. Any semantic change requires plan review and a Deviation Log row.
3. Remove paragraphs that explicitly promise the superseded compatibility
   behavior.
4. Do not add “implemented by” links for code not changed yet.
5. Record the promotion baseline identifier.

Gate:

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py \
  tests/specs/test_spec_hygiene.py -q
bin/check-doc-paths
bin/check-dom15-fixtures
```

### Slice 1: delete isolated dead and below-floor code

Owner files:

- `weft/cli/_argv.py`, `weft/shell/`, `weft/_constants.py`
- `weft/_runner_plugins.py`
- `AGENTS.md` (merge only the stale `weft/shell` layout guidance around the
  user's pre-existing worktree edits)
- `weft/helpers/__init__.py`, `weft/commands/system.py`
- `docs/ruff-suppression-registry.md` for suppression removal made possible by
  the SimpleBroker compatibility-branch deletion
- `weft/core/taskspec/model.py`
- `weft/core/tasks/multiqueue_watcher.py`, `weft/core/tasks/base.py`
- `weft/core/resource_monitor.py`
- `extensions/weft_microsandbox/weft_microsandbox/_runtime.py`
- `bin/release.py`, `bin/ruff_suppression_index.py`
- their targeted tests and benchmarks

Actions:

1. Delete the orphan CLI parser and shell package with their tests/constants.
2. Delete TaskSpec default shims and watcher turn-count state.
3. Remove Python, SimpleBroker, psutil, and Microsandbox below-floor branches.
4. Remove deprecated script flags.
5. Retarget the sole test-fixture facade consumer and delete the facade.
6. Do not alter live subprocess command preparation or watcher timing.

Gate:

```bash
./.venv/bin/python -m pytest tests/taskspec/test_taskspec.py \
  tests/tasks/test_multiqueue_watcher.py tests/tasks/test_tasks_simple.py \
  tests/system/test_helpers.py \
  tests/system/test_release_script.py \
  tests/specs/test_ruff_policy.py \
  tests/specs/test_ruff_suppression_index.py \
  tests/core/test_runner_plugins.py \
  tests/specs/resource_management/test_resource_metrics.py \
  tests/commands/test_status.py \
  tests/core/test_manager.py \
  tests/tasks/test_task_monitor.py \
  extensions/weft_microsandbox/tests -q
```

### Slice 2: collapse public paths and command ownership

Owner files:

- package initializers and bootstrap/entry modules in P1-P8 and P10-P12
- `weft/commands/system.py`, named system-operation command modules
- `weft/core/manager_runtime.py`, `weft/commands/manager.py`
- `docs/ruff-suppression-registry.md`
- import-boundary and command tests

Actions:

1. Remove compatibility entrypoints and lazy root/commands/core exports.
2. Repoint all production imports before deleting status/task-evidence
   facades.
3. Move private system-operation implementations into their named public
   modules. Move typed builtins/tidy/dump/load operations out of
   `commands.system` so that module becomes the status owner only.
4. Move `format_runner_diagnostics` into `commands.tasks`, keep its delegation
   to core `diagnostic_summary`, retarget `cli/app.py` and the diagnostics
   tests, and delete `commands/diagnostics.py`.
5. Promote manager-runtime implementations to their public names; delete the
   second wrapper layer and manager command alias table.
6. Remove affected RUF/S102 inventory suppressions and registry entries only
   after their code no longer needs them.
7. Update spec mappings and `11-CLI_Architecture_Crosswalk.md`.

Gate:

```bash
./.venv/bin/python -m pytest tests/architecture/test_import_boundaries.py \
  tests/commands/test_status.py tests/commands/test_task_evidence.py \
  tests/commands/test_dump_load.py tests/commands/test_diagnostics.py \
  tests/commands/test_manager_commands.py tests/core/test_client.py \
  tests/cli/test_cli.py tests/cli/test_cli_serve.py \
  tests/cli/test_env_file_bootstrap.py -q
```

### Slice 3: consolidate run, task-monitor, and pruning code

Owner files:

- `weft/commands/run.py`, `weft/cli/run.py`
- `weft/commands/_streaming.py`
- `weft/commands/task_monitor.py`
- `weft/core/monitor/runtime.py`, `weft/core/task_evidence.py`
- `weft/core/monitor/policies/task_log.py` and deletion of
  `weft/core/monitor/policies/types.py`
- new canonical `weft/commands/prune.py` and deletion of
  `weft/commands/runtime_prune.py` and `weft/commands/retention_prune.py`
- `weft/core/pruning/runtime.py`,
  `weft/core/pruning/retention.py`,
  `weft/core/pruning/models.py`, `weft/core/queue_window.py`

Actions:

1. Characterize foreground versus supervised malformed-row, missing-TID,
   limit, and count semantics before moving reduction.
2. Move only the three proven common pure evidence rules to
   `core/task_evidence.py`. Keep the two result types and reducers separate
   unless the characterization proves full observable equivalence.
3. Move prune family dispatch plus context/render/report/exit concerns into
   `commands/prune.py`. Delete the two compatibility command facades. Keep
   typed selection/apply/archive safety in the two core policy modules.
   Retention core keeps the narrow record encoder/writer shared by its
   mandatory archive and optional report; moving it upward would either invert
   the layer or duplicate the archive format.
4. Remove run compatibility/test wrappers and the streaming Typer shim.
5. Remove `run --monitor`.
6. Require explicit prune family. Verify no deletion happens when it is
   absent.
7. Move `CleanupPolicyRun` into its sole task-log policy consumer and delete
   the shallow types module.

Stop if unifying foreground and supervised reduction would change a documented
count or silently drop malformed-row diagnostics. In that case share only the
three proven pure parsing helpers.

Gate:

```bash
./.venv/bin/python -m pytest tests/commands/test_run.py \
  tests/cli/test_cli_run.py tests/cli/test_cli_run_installed_entrypoint.py \
  tests/commands/test_result.py tests/commands/test_task_monitor.py \
  tests/core/test_task_monitoring.py \
  tests/commands/test_runtime_prune.py \
  tests/commands/test_retention_prune.py \
  tests/cli/test_cli_system.py \
  tests/core/test_task_evidence_properties.py \
  tests/core/test_task_monitor_cleanup.py \
  tests/core/monitor/policies/ tests/core/test_pruning_apply.py \
  tests/test_long_session_surface_benchmark.py \
  tests/tasks/test_task_monitor.py -q
```

### Slice 4: make TaskSpec and resource-monitor contracts strict

Owner files:

- `weft/core/taskspec/model.py`
- new `weft/core/taskspec/transport.py`
- `weft/core/spawn_requests.py`, `launcher.py`, `pipelines.py`,
  `manager.py`, and `tasks/runner.py`
- `weft/ext.py`, `weft/core/runner_validation.py`,
  `weft/core/environment_profiles.py`, `weft/core/agents/validation.py`, and
  first-party runner plugin validation implementations
- `weft/commands/submission.py`, `run.py`, and `specs.py`
- `weft/core/agents/tools.py`
- `weft/core/resource_monitor.py`
- `weft/core/runners/host.py`,
  `weft/core/runners/subprocess_runner.py`
- TaskSpec, CLI validation, resource-monitor, runner, and agent tests

Actions:

1. Require explicit provider CLI authority.
2. Remove `approval_required` through resolved tool models.
3. Reject unknown top-level TaskSpec fields.
4. Implement the three transport helpers named in `7.2. Replace every direct
   attach/read of `_weft_bundle_root`: stored/bundle callers pass
   `bundle_root=`, transport ingress decodes and strips, and queue/process
   egress encodes and reattaches. Partial runner-plugin validation receives an
   explicit `bundle_root` keyword instead of a reserved mapping key. Then make
   the model strict.
5. Make `ResourceMonitor` the concrete psutil class with one method family and
   constructor. Delete the uncalled `get_max_metrics` method rather than carry
   an extra method outside the current protocol.
6. Add a constructor-body `TypeError` test proving the loader calls once and
   propagates.

Gate:

```bash
./.venv/bin/python -m pytest tests/taskspec/test_taskspec.py \
  tests/specs/taskspec/test_agent_taskspec.py \
  tests/core/test_spawn_requests.py \
  tests/core/test_spec_parameterization.py \
  tests/commands/test_specs.py tests/cli/test_cli_validate.py \
  tests/specs/resource_management tests/core/test_subprocess_runner.py \
  tests/tasks/test_runner.py tests/tasks/test_agent_execution.py \
  tests/core/test_manager.py tests/core/test_pipelines.py \
  tests/core/test_runner_validation.py tests/core/test_agent_resolution.py \
  tests/core/test_agent_tools.py tests/core/test_provider_cli_backend.py \
  tests/core/test_provider_cli_execution.py \
  tests/core/test_provider_cli_session_backend.py \
  tests/core/test_tool_profiles.py tests/core/test_spec_run_input.py \
  tests/system/test_manager_process.py tests/cli/test_cli_pipeline.py \
  tests/commands/test_submission.py tests/commands/test_run.py \
  extensions/weft_docker/tests extensions/weft_microsandbox/tests \
  extensions/weft_macos_sandbox/tests -q
```

### Slice 5: cut the control protocol over atomically

Owner files:

- `weft/core/control_messages.py`
- `weft/core/tasks/base.py`
- `weft/core/tasks/consumer.py`, `pipeline.py`
- `weft/core/manager.py`, `manager_runtime.py`,
  `control_probe.py`
- `weft/commands/tasks.py`, `run.py`
- control, task, pipeline, manager, and command tests

Actions:

1. Define one encoder and one strict parser in `control_messages.py`.
2. Change every Weft-owned control producer in the same slice.
3. Remove raw parsing, raw STOP/KILL branches, and raw control tests.
4. Keep malformed/non-object payload tests as negative protocol tests.
5. Require all manager authority fields in the one shared PONG gate.
6. Preserve keyed request ownership, ACK versus terminal meaning, deferral,
   queue cleanup, and manager drain.
7. Move the request value type into the wire module and drop its unused raw
   copy. Invalid rows are exact-acknowledged without dispatch so they cannot
   block later valid controls. Make manager drain visible as
   `should_stop=true` in its PONG snapshot without forcing the owner loop to
   exit early.

No mixed raw/JSON transition is allowed. A temporary dual writer or dual
reader would recreate the policy violation this slice removes.

Gate:

```bash
./.venv/bin/python -m pytest tests/core/test_control_probe.py \
  tests/tasks/test_control_channel.py \
  tests/commands/test_task_commands.py \
  tests/tasks/test_task_interactive.py \
  tests/tasks/test_task_observer_behavior.py \
  tests/tasks/test_task_observability.py \
  tests/tasks/test_task_execution.py \
  tests/tasks/test_pipeline_runtime.py \
  tests/core/test_manager.py tests/core/test_control_messages.py \
  tests/commands/test_run.py tests/commands/test_manager_commands.py \
  tests/tasks/test_heartbeat.py tests/tasks/test_task_monitor.py -q
```

### Slice 6: canonicalize manager/service state

Owner files:

- `weft/_constants.py`
- `weft/core/service_convergence.py`
- `weft/core/manager.py`
- `weft/core/manager_runtime.py`
- `weft/core/manager_services.py`
- `weft/commands/system.py`
- `weft/core/monitor/task_monitor.py`
- manager/service/status tests

Actions:

1. Change `SERVICE_OWNER_SCHEMA` to `weft.service_owner.v2` and build/parse
   only that persisted shape.
2. Add only the small `discard_v1_service_registry_rows(queue)` bootstrap
   helper specified in `7.4. Call it immediately after each existing service
   registry queue open, before the queue reaches manager, manager-runtime,
   status, or Monitor logic. Do not add a registry connection class, row
   transformer, schema framework, or v1 parser.
3. Keep manager-specific construction as a domain adapter over the generic
   builder; do not copy the schema.
4. Derive public status projection fields from the canonical row.
5. Remove duplicate queue/identity fields and `legacy_role`.
6. Make `ManagedServiceState` the sole autostart lifecycle state.
7. Delete Manager's signal alias and unused manager-event argument.
8. Remove duplicate production PID delegates.

There is no row transformation or durable migration for `weft.state.services`
because it is explicitly runtime-only. The cutover requires all old processes
to stop. Bootstrap discards v1 rows, live owners rebuild v2 rows, and normal
readers remain strict.

Gate:

```bash
./.venv/bin/python -m pytest tests/core/test_service_convergence.py \
  tests/core/test_control_probe.py tests/core/test_manager_services.py \
  tests/core/test_manager.py tests/commands/test_manager_commands.py \
  tests/commands/test_status.py tests/cli/test_cli_manager.py \
  tests/commands/test_run.py tests/commands/test_serve.py \
  tests/commands/test_runtime_prune.py tests/cli/test_cli_run.py \
  tests/cli/test_cli_serve.py tests/cli/test_status.py \
  tests/tasks/test_task_monitor.py -q
```

### Slice 7: migrate Monitor storage and external records

Owner files:

- `weft/_constants.py` for the schema version
- `weft/core/monitor/sql.py`, `store.py`, `task_monitor.py`
- `weft/core/monitor/external_log.py`, `lifetime_report.py`
- Monitor/task-monitor tests

First build a writer-reachability table for every repair selector:

| State | Current writer/interruption can create it? | Action |
|---|---|---|
| pending current child ref with raw row | yes | keep current repair |
| current parent marked raw-deleted with child refs after interrupted cleanup | verify | keep if reachable; otherwise migrate/delete |
| old child `deleted_at_ns` tombstone | no after current physical-delete writer | migrate once, delete normal-cycle lane |
| old integer in an owned canonical-string JSON field | no | rewrite in schema migration |
| missing/deferred write after current crash | yes | keep |
| `result_without_terminal` after wrapper/log gap | yes | keep |

Actions:

1. Bump the Monitor schema version from 5 to 6 and the external task-log schema
   version from 1 to 2. Do not change the separate foreground checkpoint
   schema merely because it has the same current number.
2. Add the exact absent/unversioned/v5/v6/older/future dispatcher from `7.6.
   Do not merely rewrite version metadata as `ensure_schema()` does today.
3. In one sidecar transaction, rewrite only the owned JSON ID fields listed in
   [SB-0.4a], advance pending deferred external envelopes from schema version 1
   to 2 without traversing opaque payload fields, reconcile verified obsolete
   tombstones and the old parent-marker state, validate the result, and then
   advance the version.
4. Make normal readers strict for the new at-rest representation.
5. Delete old-release-only normal-cycle queries, methods, counters, comments,
   and tests, including child tombstone pruning and the parent-raw-deleted with
   surviving-child-ref repair lane.
6. Keep current-writer recovery proven by the reachability table.
7. Emit `task_summary`/`service_summary` externally and remove the compatibility
   record type.

Stop and re-plan if the active SimpleBroker sidecar API cannot make the
migration atomic on every supported SQL backend. Do not fall back to an
always-tolerant reader.

Gate:

```bash
./.venv/bin/python -m pytest tests/core/test_monitor_store.py \
  tests/core/test_monitor_external_log.py \
  tests/core/monitor/test_lifetime_report.py \
  tests/tasks/test_task_monitor.py \
  tests/core/test_task_monitoring.py \
  tests/commands/test_task_monitor.py -q
```

### Slice 8: make context and file writes honest

Owner files:

- `weft/context.py`
- `README.md`
- `weft/helpers/__init__.py`
- `weft/core/agents/provider_cli/settings.py` and advisory health caller
- context, helper, CLI, and agent-settings tests

Actions:

1. Remove legacy SQLite upward discovery.
2. Make missing config creation and existing invalid config failure distinct.
3. Use the atomic writer for config creation where it fits the current owner
   contract.
4. Remove the direct-write fallback. Retry only the bounded replace cases
   already designated retryable, clean the temp file, and propagate.
5. Assert the pre-existing target bytes remain unchanged on replace failure.
6. Preserve advisory caller behavior only at the caller that already owns a
   best-effort health write.
7. Unify `WEFT_LOGGING_ENABLED` with the current boolean normalizer.

Gate:

```bash
./.venv/bin/python -m pytest tests/context/test_context.py \
  tests/system/test_helpers.py tests/system/test_constants.py \
  tests/cli/test_env_file_bootstrap.py \
  tests/core/test_provider_cli_settings.py -q
```

### Slice 9: remove inert runtime identity and parameters

Owner files:

- `weft/core/runners/outcome.py` and all first-party runner producers
- `weft/core/tasks/consumer.py`
- `weft/commands/tasks.py`, `client/_namespaces.py`
- `weft/core/manager_runtime.py`
- `weft/commands/submission.py`
- runner, task, client, manager, extension, and parity tests

Actions:

1. Inventory every `RunnerOutcome` producer and consumer.
2. Remove `worker_pid` only if `runtime_handle` plus the live
   `on_worker_started` callback covers every identity use. If not, record a
   Deviation Log row and retain it as independent current state.
3. Remove the three inert parameters without changing real CLI projection.
4. Remove the first-party extension package-root compatibility exports and
   retarget extension tests to canonical plugin entrypoints.

Gate:

```bash
./.venv/bin/python -m pytest tests/tasks/test_runner.py \
  tests/tasks/test_task_observability.py \
  tests/core/test_client.py tests/commands/test_task_commands.py \
  tests/commands/test_manager_commands.py \
  extensions/weft_docker/tests extensions/weft_macos_sandbox/tests \
  extensions/weft_microsandbox/tests \
  tests/tasks/test_command_runner_parity.py -q
```

### Final slice: traceability and whole-tree verification

Actions:

1. Re-run `rg` for `compat`, `legacy`, `deprecated`, aliases, old module
   paths, old control writes, and removed schema keys. Classify every remaining
   match as a current rejection test, platform/capability path, historical
   plan, or unresolved violation.
2. Update every touched spec implementation mapping and reciprocal module
   docstring.
3. Update `docs/ruff-suppression-registry.md` and its generated index/checks.
4. Update `docs/lessons.md` only if implementation exposes a repeatable new
   lesson. Do not add a restatement of this plan.
5. Close every Deviation Log row and every review rework item.
6. Change this plan and index to `completed` only after implementation is
   committed or explicitly hand off the uncommitted state as draft.

## 9. Test Disposition

Delete tests that exist only to pin removed code:

- argument rearrangement and known-interpreter suites
- lazy facade identity/export inventory cases
- TaskSpec default shim tests
- `collect_status`, `cmd_run`, run wrapper, Typer shim, and Manager signal
  alias cases
- only the long-method bridge and no-argument-constructor retry assertions in
  `tests/specs/resource_management/test_monitor_compat.py`, the legacy
  `Process.connections()` assertion in `test_resource_metrics.py`, the
  Microsandbox name-method case, old Monitor tombstone normal-cycle case, and
  deprecated script flag cases
- `python -m weft.cli` parameter cases

Retarget tests without weakening their behavior assertions:

- status to `commands.system`
- task evidence to `core.task_evidence`
- system operations to their named public command owners
- run tests to structured execution results and separate rendering tests
- Manager signals to `note_termination_signal`
- all valid control writes to the shared JSON encoder
- manager command patching to public manager-runtime functions
- resource-monitor paths to the one real `ResourceMonitor` class; retain and
  retarget the no-broker-queue metrics proof, process-disappearance behavior,
  one-call constructor defect propagation, current `net_connections()`
  behavior, enforcement rules, and idempotent stop
- extension imports to leaf plugin entrypoints

Replace compatibility-pinning tests with strict negative or migration tests:

- omitted provider authority fails in model and black-box CLI validation
- both `approval_required` values fail as unknown
- unknown top-level TaskSpec keys fail; bundle transport still works
- each missing PONG authority field fails selection proof
- persisted service rows have the exact nested shape and lack aliases
- resource-monitor constructor `TypeError` is not retried
- internal SimpleBroker API `TypeError` is not retried through an old signature
- missing prune family fails before mutation
- corrupt config fails without byte changes or traceback
- atomic replace failure propagates, preserves target bytes, and cleans temp
  files
- old Monitor schema migrates once; the current reader then rejects noncanonical
  current-version data

Keep rejection tests for removed old shapes and paths. They prove strictness and
must not be mistaken for compatibility support. Keep all current recovery
tests whose setup remains reachable from a current writer or interrupted
current transaction.

## 10. Adversarial Acceptance Probes

The implementation must include:

- installed `weft` and `python -m weft` entrypoint probes with env-file loading
- old import paths fail while `weft.client` and canonical leaf imports succeed
- raw `ctrl_in` text cannot stop, kill, pause, or otherwise control a task;
  canonical envelopes still do
- JSON STOP/KILL deferral, keyed reply ownership, ACK-only nonterminal behavior,
  manager drain, and pipeline control all fire through real queues/processes
- omitted provider authority, removed approval field, and unknown top-level
  TaskSpec field fail with useful CLI errors and no traceback
- prune without family performs no mutation; explicit dry-run and apply retain
  current protections
- service-registry bootstrap exact-deletes v1 rows, preserves v2 rows and
  message IDs, is idempotent, fails if v1 reappears during verification, and
  exposes only strict v2 data after a quiescent restart
- migration from the immediately prior Monitor schema is idempotent; a forced
  failure leaves version and data unchanged
- corrupt project config and atomic replace failure preserve original bytes
- first-party Docker, macOS sandbox, and Microsandbox runner parity still uses
  runtime handles and current monitor semantics

Mocked unit tests do not replace the real broker/manager/process proofs where
the existing harness can exercise them.

## 11. Rollout, One-Way Doors, and Rollback

This release cannot support mixed old and new Weft processes.

Cutover:

1. Stop all managers, TaskMonitors, persistent tasks, pipelines, and other
   Weft processes for the context.
2. Back up the broker/Monitor database and relevant `.weft` metadata.
3. Install one code version across CLI, manager, workers, and first-party
   extensions.
4. Run the Monitor schema migration before starting ordinary Monitor reads.
5. Let service-registry bootstrap exact-delete v1 rows, then rebuild
   runtime-only `weft.state.services` through normal v2 manager/service
   heartbeat. Do not transform old rows or accept them in normal readers.
6. Update external JSONL consumers for the new record types at the same
   cutover.
7. Resume managers and verify canonical PONG/service records before submitting
   work.

One-way doors:

- once new JSON control messages are in flight, an old process cannot consume
  them safely.
- once the Monitor schema version advances, old code must not open the store.
- service-owner rows and external record types change without dual writers.
- removed Python imports and CLI options fail immediately.

Rollback is only supported by stopping all new processes and restoring the
pre-migration backup with the old code. Do not add dual readers, dual writers,
schema aliases, or compatibility shims to make rollback live.

## 12. Full Verification

After each targeted gate passes:

```bash
. ./.envrc
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft bin integrations/weft_django/weft_django \
  extensions/weft_docker/weft_docker \
  extensions/weft_macos_sandbox/weft_macos_sandbox \
  extensions/weft_microsandbox/weft_microsandbox \
  --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/ruff format --check .
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py \
  tests/specs/test_spec_hygiene.py -q
bin/check-doc-paths
bin/check-dom15-fixtures
bin/coalesce-check
```

Before spec promotion or production edits, capture the baseline report and
record its result in the Execution Log:

```bash
../backstitch/.venv/bin/backstitch check --repo-root . --no-config \
  --spec-root docs/specifications --plan-root docs/plans \
  --code-root weft --code-root tests --code-root bin \
  --code-root integrations --code-root extensions --format json \
  --output /tmp/weft-canonical-cleanup-before.json || test $? -eq 1
```

After the final implementation and mapping edits, capture the comparison
report:

```bash
../backstitch/.venv/bin/backstitch check --repo-root . --no-config \
  --spec-root docs/specifications --plan-root docs/plans \
  --code-root weft --code-root tests --code-root bin \
  --code-root integrations --code-root extensions --format json \
  --output /tmp/weft-canonical-cleanup-after.json || test $? -eq 1
```

Repository-wide historical debt may keep backstitch nonzero. The acceptance
gate is zero new issue keyed to a touched spec section, plan, code path, test,
or removed path, plus removal of every stale mapping for this scope. If the
backstitch executable is absent, report that as an unpassed gate; do not silently
replace it with grep.

## 13. Independent Review Protocol

Before spec promotion, give a fresh reviewer this prompt:

> Read
> `docs/plans/2026-08-10-canonical-contract-and-dead-code-cleanup-plan.md`,
> including its Proposed Spec Delta, ownership inventory, test disposition,
> migration, and rollout. Read the governing spec sections and inspect the
> named current code. Look for missed compatibility paths, false-positive
> deletions, two owners left behind, tests that would be weakened instead of
> retargeted, current crash-repair paths misclassified as old-release support,
> non-atomic migration, and mixed-version gaps. Return PASS or a prioritized
> rework list with exact paths and section references.

Repeat an independent review after the control/service slice and after the
Monitor migration slice. Run a final fresh-eyes review over the whole diff
before completion.

## 14. Retained Boundaries and Explicit Non-Goals

Out of scope:

- new commands, features, runners, plugins, queue semantics, or state models
- changing public result shapes not named in Section 7
- redesigning the extension API or promoting all first-party core helpers to a
  stable third-party API
- merging runtime and retention prune policy
- changing the SimpleBroker integer-or-canonical-string exact-ID ingress
  contract
- removing current crash recovery, `result_without_terminal`, or forced-process
  cleanup
- replacing queue-native waiting or platform capability fallbacks
- refactoring large cohesive classes due to size alone
- historical-plan cleanup

First-party extensions remain version-coupled parts of Weft. `weft.ext` owns
the declared runner protocols and handles, but this plan does not manufacture
an arm's-length plugin framework around internal helpers they currently need.
Update spec wording only enough to make that boundary honest.

### Refactor punch-list follow-up (2026-08-10)

Task classification: Class 5. The follow-up changes the exact context and
Monitor-v6 contract text while finishing deletion and ownership work already
inside this plan. Hardening remains governed by the existing Class 5 plan.

Source specs: `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4],
[SB-0.4a]; `docs/specifications/05-Message_Flow_and_State.md` [MF-3];
`docs/specifications/07-System_Invariants.md` [QUEUE.2a];
`docs/specifications/10-CLI_Interface.md` [CLI-0.2].

Required outcomes:

1. Pin or restore the pre-cleanup root chosen for an absolute
   `BROKER_PROJECT_CONFIG_PATH`; do not silently relocate `.weft/`.
2. Preserve the primary atomic-replace error even if temporary-file cleanup
   also raises an `OSError`.
3. Accept only canonical `weft.service_owner.v1` and `.v2` schema suffixes;
   numeric lookalikes are malformed, not versions.
4. Delete the unused Monitor raw-deletion method and SQL statement. Make the
   two retained-or-removed v6 delete-state columns an explicit current-schema
   decision before landing.
5. Complete the named cosmetic ownership, documentation, and adversarial
   entrypoint probes without adding compatibility aliases or a new migration
   framework.

Invariants that must not move: current context discovery remains Weft-scoped;
atomic writes either publish the complete new bytes or preserve the old bytes;
live service readers remain v2-only; v5-to-v6 remains the sole Monitor
migration edge; current v6 startup remains non-mutating and exact; CLI and
control behavior remain unchanged.

Verification: focused RED-to-GREEN tests per edge, full affected suites,
SQLite plus PostgreSQL when Monitor persistence changes, full Ruff/format/mypy,
spec and doc-path gates, final Backstitch comparison, and an independent
fresh-eyes review.

## 15. Execution Log

| Date | Slice | Baseline / evidence | Result | Notes |
|---|---|---|---|---|
| 2026-08-10 | Plan authoring | `3bc9bbd1e9e67fcd91716dfccda3a9efd13115f2` plus current worktree inventory | complete | Read-only architecture, spec, duplicate-owner, and test-disposition passes. No production files changed. |
| 2026-08-10 | Independent plan review | full draft and two corrective reviews against current code/specs | PASS | Ten findings were corrected. After the owner chose bootstrap discard/rebuild instead of row transformation, the reviewer confirmed the bounded v1 deletion helper leaves normal logic v2-only. |
| 2026-08-10 | Spec promotion | `3bc9bbd1e9e67fcd91716dfccda3a9efd13115f2` plus reviewed uncommitted 13-spec promotion diff; before report `/tmp/weft-canonical-cleanup-before.json` | complete | 357 sections, 1,270 code refs, 753 mappings, 45 errors, 1,023 warnings, 605 infos before promotion. Metadata, hygiene, path, DOM-15, and whitespace gates passed. Implementation-only crosswalk mappings remain with their owning slices. |
| 2026-08-10 | Slice 1 | deletion diff plus temporary-index gate | complete | 788 tests passed, one PostgreSQL-only test skipped. Ruff, format, full mypy, suppression registry, doc paths, collection, and independent review passed. Removed generated shell-package bytecode residue before import probes. |
| 2026-08-10 | Slice 2 | package/command/manager ownership diff plus temporary-index integration gate | complete | Combined entrypoint, client, command, manager-runtime, dump/load, evidence, and runner-parity gate passed. Full Ruff, format, mypy, spec/path/suppression checks passed. Independent review found three mapping-only P2s; all were fixed and focused re-review passed. |
| 2026-08-10 | Slice 3 | run, monitor-helper, and prune ownership consolidation diff | complete | Full Slice 3 gate, Ruff, format, targeted mypy, spec hygiene, and path checks passed. Independent review found report-eligibility, rejection-probe, preservation-test, and ownership-map gaps; all were fixed. Focused re-review passed 47 probes and a final fresh-eyes review returned PASS. |
| 2026-08-10 | Slice 4 | strict TaskSpec transport and ResourceMonitor consolidation diff | complete | Combined Slice 4 gate passed from final pre-review state. Review found strict nested-model, manager process transport, parameterization provenance, CLI/plugin coverage, and exact resource-threshold test gaps; all were fixed. Final TaskSpec re-review passed 165 non-control tests, final ResourceMonitor re-review passed 18 mutation-sensitive tests, and both returned PASS with Ruff/mypy clean. |
| 2026-08-10 | Slice 5 | strict control-envelope and Manager PONG authority cutover diff | complete | Final exact gate passed 1,068 tests. Full Ruff, format, mypy, import-boundary, doc-path, and whitespace gates passed. Review found drain-time control starvation, unhashable and duplicate-key PONG failures, exact-ACK/keyed-echo test gaps, and stale ownership docs; all were fixed and independent re-review returned PASS. |
| 2026-08-10 | Slice 6 | strict v2 service-registry and Manager lifecycle ownership diff | complete | Final independent gate passed 794 Slice 6 tests plus 122 architecture/constants/launcher tests. Ruff, format, mypy, spec hygiene, doc paths, and whitespace checks passed. Review found Unicode decimal acceptance, a stale signal-alias mapping, and missing race/idempotence/live-surface probes; all were fixed and independent re-review returned PASS. |
| 2026-08-10 | Slice 7 | strict Monitor v6 migration/current-schema and external-v2 diff | complete | Final expanded SQLite gate passed 345 tests. Post-repair rollback and raw-present rejection probes passed in disposable PostgreSQL 18 containers. Full Ruff, format, mypy, architecture, doc-path, and whitespace checks passed. Independent review found current-v6 DDL repair, missing raw-state proof, obsolete-index residue, stale repair prose, and enumerable structural/data probe gaps; all were fixed and final re-review returned PASS. |
| 2026-08-10 | Slice 8 | context discovery, config integrity, and atomic-write cleanup diff | complete | Final focused gate passed 182 tests. Ruff, format, mypy, spec hygiene, and doc-path checks passed. Independent review found and verified fixes for empty supplied config re-enabling root `.broker.toml` discovery, stale discovery/config wording, and missing durable hostile probes; final re-review returned PASS. |
| 2026-08-10 | Slice 9 | inert runtime identity/parameter and extension-export cleanup diff | complete | Final independent gate passed 556 Slice 9 tests and 566 with the CLI process-rendering preservation additions. Ruff, format, mypy, spec/import hygiene, doc paths, and whitespace checks passed. Review found only reciprocal CC-3/CC-3.2/CLI-1.2 mapping precision gaps; all were fixed and independent re-review returned PASS. |
| 2026-08-10 | Final integration | current uncommitted worktree; intended tracked deletions represented only in an isolated temporary Git index | verified, uncommitted | Default full suite passed 3,964 tests with 2 skips; all-marker suite passed 3,965 with 13 skips. Full Ruff, format, mypy (186 sources), spec/plan hygiene, doc paths, DOM-15, coalescing, suppression-ledger, whitespace, and real-index-empty checks passed. Slice 7 passed 345 SQLite tests plus the post-repair rollback and raw-present rejection probes in disposable PostgreSQL 18 containers. Final Backstitch comparison has zero new normalized findings and reduced repository totals from 45 errors / 1,023 warnings / 605 infos to 26 / 925 / 567. |
| 2026-08-10 | Refactor punch-list follow-up | current uncommitted follow-up diff over the verified cleanup worktree | verified, uncommitted | Default full suite passed 3,978 tests with 2 skips; all-marker suite passed 3,979 with 13 skips. Full Ruff, format, mypy (186 sources), plan/spec hygiene, doc paths, DOM-15, coalescing, suppression-ledger, whitespace, and real-index-empty checks passed. Backstitch retained zero new normalized findings and 149 removed findings. PostgreSQL was not rerun because the follow-up deleted only a zero-caller Monitor surface and made no schema, migration, or persistence behavior change. Independent follow-up review returned PASS. |

## 16. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|
| D1, Slice 3 | Move only the three named task-log timestamp, monitor-failure classification, and TaskSpec-name parsers into `core/task_evidence.py`. | The exact duplicate generic `status_from_log_payload` parser also moved from Monitor runtime and retention pruning into the same core owner. Reducers and policy-specific classification remained separate. | The fourth function was byte-equivalent domain parsing with two real core consumers. Leaving it duplicated would contradict the cleanup objective, while moving it did not merge divergent policy. | None. This is implementation ownership under the existing task-evidence contracts. |
| [SB-0.4a], [MF-5], [OBS.13] | Add the bounded v5-to-v6 migration and reject unsupported versions; the initial plan did not enumerate every physical-schema and raw-state verification probe later required by review. | Review expanded current-v6 verification to exact ordered tables, columns, primary keys, and Monitor-owned indexes, and expanded v5 migration preflight/rollback probes to prove raw-row absence before tombstone deletion. | Without the expansion, current v6 startup silently repaired schema and migration could mark a still-present raw row deleted. The added checks enforce the already intended strict current contract rather than add a compatibility path. | Applied to `04-SimpleBroker_Integration.md` [SB-0.4a] and synchronized cleanup wording in [MF-5] / [OBS.13]. |

## 17. Review Rework Log

| Review | Finding | Disposition | Plan section |
|---|---|---|---|
| Independent ownership map | Keep runtime and retention policies, foreground and supervised Monitor policy, persisted and projected service shapes, platform fallbacks, and current-writer recovery separate. | Accepted in scope rules, ownership inventory, stop gates, and non-goals. | Sections 4, 6, 8, 14 |
| Independent test map | Retarget current invariant tests; delete only compatibility-only tests; replace compatibility assertions with strict negatives or migration tests. | Accepted. | Section 9 |
| Independent spec map | The current specs explicitly bless several paths, so spec promotion must precede deletion. | Accepted. | Sections 3, 7, 8 |
| Plan review P0 | TaskSpec bundle transport had two unresolved designs. | Accepted. Named `taskspec/transport.py` and exact encode/decode/validate ownership and lifecycle. | Sections 6.3, 7.2, Slice 4 |
| Plan review P0 | Monitor migration accepted an open-ended set of older stores. | Accepted. Limited support to new/empty and v5-to-v6; all other versions fail. | Section 7.6, Slice 7 |
| Plan review P0 | Backstitch before-state was scheduled too late. | Accepted. It is the first spec-promotion action and precedes every edit. | Spec-promotion slice, Section 12 |
| Plan review P1 | `weft/cli/__init__.py` facade was omitted. | Accepted. Added to P1 and Slice 2 package cleanup. | Section 6.1, Slice 2 |
| Plan review P1 | TaskMonitor result/reducer consolidation overclaimed equivalence. | Accepted. Only three proven parsers move unless characterization proves all observable semantics. | D1, Slice 3 |
| Plan review P1 | `monitor/policies/types.py` lacked a second consumer. | Accepted. Move its one dataclass to `task_log.py` and delete it. | Section 6.1, Slice 3 |
| Plan review P1 | Diagnostics destination was unnamed. | Accepted. Named `commands.tasks` as presentation owner over core summary logic. | P6, Slice 2 |
| Plan review P2 | Resource-monitor test deletion was ambiguous. | Accepted. Named the deleted compatibility assertions and retained invariants. | Section 9 |
| Plan review P2 | Neutral UUID/slicing helpers would create a weak abstraction. | Accepted. Inline them; share only domain-owned age/status rules. | D2, Slice 3 |
| Plan review P2 | Required-reading runbook path was wrong. | Accepted. Corrected to `runtime-and-context-patterns.md`. | Section 2 |
| Owner clarification | v2 registry connection should migrate automatically so live code sees only the current contract; discard/rebuild is preferable when viable; minimize new code. | Accepted and independently re-reviewed: PASS. One small pre-live helper exact-deletes v1 rows, verifies they are gone, and leaves existing v2 rows untouched. No transformer, connection abstraction, or v1 live parser. | Section 7.4, Slice 6, rollout and probes |
| Slice 4 implementation map | Runner plugins validate partial TaskSpec-shaped mappings, so the full strict transport decoder cannot own their bundle provenance. | Accepted before spec promotion completed. Pass `bundle_root` through the existing runner-validation interface; keep `_weft_bundle_root` exclusive to full TaskSpec queue/process transport. Expanded the direct-consumer gate. | Section 7.2, Slice 4 |
| Slice 1 implementation map | The original owner list omitted test discovery references, the Ruff suppression ledger, snapshot-ledger consumers, and direct dependency-floor tests. | Accepted before production edits. Added the ledger owner and expanded the gate; retained watcher timing, platform resource adapters, current exact-ID normalization, and Microsandbox attribute-failure handling. | Slice 1, Sections 9 and 14 |
| Slice 2 implementation map | `manager_runtime` contains 16 private/public function or class identities, and `commands.submission.generate_tid` is another dead wrapper. | Accepted before Slice 2 edits. Rename all real runtime implementations to their public names, delete every twin and command alias, and preserve real presentation helpers and Slice 3 run seams. | P11-P12, Slice 2 |
| Slice 3 implementation map | Retention archive safety shares its record encoder with optional reports; current run IDs are timestamp/nanoseconds/PID strings, not UUIDs; the gate omitted direct cleanup/pruning consumers. | Accepted before Slice 3 edits. Keep the narrow retention encoder in core, preserve run-ID format while inlining, and expand the gate. | D2-D3, Slice 3 |
| Slice 3 review | Moving report orchestration changed which early failures create or truncate reports. Exact removed-surface probes and preservation tests were incomplete, and the CLI crosswalk omitted the new owners. | Accepted and fixed. Core results identify early halt stages; command reporting retains each prior policy boundary. Added exact `--monitor` and deleted-module rejection probes, exact run-ID/age/limit tests for both policies, core-owner imports, and complete system ownership mapping. Independent re-review passed. | D2-D3, Slice 3 |
| Slice 4 TaskSpec review | Five nested public models still ignored extras; parameterization and manager process egress bypassed the transport owner; one manager-process defect test patched a dead seam; black-box, pipeline, Docker, and mapping coverage was incomplete. | Accepted and fixed. All public models forbid extras; stored parameterization and both manager process hops use the three transport helpers; tests target current seams and fire CLI, pipeline, Docker, and process-boundary behavior. Final re-review passed. | D4, Slice 4 |
| Slice 4 ResourceMonitor review | Exact 4-of-5 CPU, recursive aggregation, first-sample connection enforcement, and both removed loader arguments were not fully mutation-killed. | Accepted and fixed with test-only probes, including both the 3-of-5 non-violation and 4-of-5 violation cases. Final mutation-focused re-review passed. | D4, Slice 4 |
| Slice 2 independent review | Runtime behavior passed, but three mappings still named the old TID owner, used pathless manager symbols that backstitch could not resolve, or lacked the reciprocal CLI marker mapping. | Fixed with path-qualified current owners and a CLI-0.2 entry/marker mapping; focused backstitch query and re-review passed. | Slice 2 traceability, [MA-2], [MA-3], [CLI-0.2] |
| Slice 5 implementation map | The plan omitted the request value owner, exact key/case rules, invalid-row progress, keyed reply echo, and the fact that a draining Manager otherwise advertises `should_stop=false`. | Accepted before Slice 5. Keep one wire-only module, exact-ack invalid rows without dispatch, echo keyed requests, and expose drain in the PONG snapshot. Expanded direct producer/consumer gates. | Section 7.3, Slice 5 |
| Slice 5 independent review | A draining Manager skipped control polling; malformed list-valued or duplicate-key PONGs could raise or become liveness proof; exact-ID ACK, every keyed reply, and ownership/mapping edges were not fully mutation-killed. | Fixed at the owning boundaries. Drain turns service control first; strict object decoding rejects duplicate authority payloads while reply cleanup retains one exact owner; tests prequeue exact IDs and enumerate keyed replies; mappings/docstrings/README now name the canonical owners. Final 1,068-test review gate passed. | Section 7.3, Slice 5 |
| Slice 6 implementation map | Registry bootstrap and strict v2 use also touch constants, manager runtime, status, and TaskMonitor; queue deletion must not occur during generator pagination. | Accepted before Slice 6. Scan fully, fail on numeric future schemas before delete, bulk exact-delete v1 IDs, verify freshly, and call at the four live interpretation surfaces. Expanded owner list and gate. | Section 7.4, Slice 6 |
| Slice 6 independent review | Unicode `.isdigit()` accepted noncanonical schema suffixes and owner TIDs; signal docs still named the deleted Manager alias; deletion reappearance, idempotence, and three live bootstrap surfaces lacked firing probes. | Fixed at the strict parser/bootstrap boundaries. Require ASCII decimal forms, map the real BaseTask/Manager signal owners, and exercise exact discard, preservation, future rejection, and fresh verification through all four production surfaces. Final 794-test re-review passed. | Section 7.4, Slice 6 |
| Slice 7 writer-reachability map | Deferred v5 rows could later flush external schema v1, while parent raw-deleted plus surviving child refs is not reachable from current physical-delete transactions. | Accepted before Slice 7. Upgrade deferred envelope versions in migration, normalize the old parent state there, delete its live repair lane, retain only current-writer recovery, and add explicit SQLite/Postgres atomicity coverage. | Section 7.6, Slice 7 |
| Slice 7 independent review | Current v6 startup performed DDL before version dispatch; missing checkpoint IDs passed verification; tombstone removal lacked raw-row absence proof; the obsolete v5 index survived; stale live-repair prose and structural/data mutation probes were incomplete. | Split non-mutating v6 physical/data verification from new/v5 DDL, require exact ordered tables/columns/PKs/indexes and every owned ID, preflight tombstones through public exact-ID reads, transactionally drop the old index, and enumerate dispatcher/migration rejection and rollback cases. Final 345-test SQLite plus post-repair PostgreSQL re-review passed. | Section 7.6, Slice 7, [SB-0.4a], [MF-5], [OBS.13] |
| Slice 8 independent review | Supplying `config={}` bypassed Weft defaults and re-enabled SimpleBroker root `.broker.toml` discovery; exact hostile edges and nearby docs were incomplete. | Fixed at the shared SimpleBroker-default owner with no fallback alias. Added durable nested-root, corrupt-config CLI, and exhausted-replace probes; synchronized config/discovery docs. Final re-review passed. | Section 7.6, Slice 8 |
| Slice 9 independent review | Runtime behavior and removed surfaces were clean, but the command/host/Docker-agent docstrings and CC-3 extension mapping did not precisely name the newly canonical live identity and leaf-factory owners. | Added reciprocal CLI-1.2/CC-3.2 citations and exact leaf `plugin.py::get_runner_plugin` mappings; package-root non-export is now explicit. Final 566-test review gate passed. | Slice 9 traceability, [CC-3], [CC-3.2], [CLI-1.2] |
| Final integration audit | Full-suite collection exposed three tests patched against the deleted command TID alias and one new shared-test module missing from the xdist sharing list. Backstitch exposed ambiguous duplicate CLI/agent section IDs and pathless or stale ownership mappings. | Retargeted tests to the canonical spawn-request timestamp owner, registered the control codec test module, assigned unique child CLI IDs, removed the duplicate agent heading ID, and made current mappings reciprocal and path-qualified. Both full suites, the isolated suppression-ledger gate, and a zero-new-finding Backstitch comparison passed without staging the real index. | Section 12, final traceability |
| Final fresh-eyes review | Two historical plans carried formatter-only churn despite the explicit historical-plan non-goal, and the unused old root `.broker.toml` filename constant survived context cleanup. | Restored both historical plans byte-for-byte and deleted `BROKER_PROJECT_CONFIG_FILENAME`. The focused constants/context/bootstrap gate passed 119 tests; final-tree Backstitch retained zero new normalized findings and 149 removed findings. The reviewer returned PASS with no remaining concrete finding. | Sections 6.4, 14, final diff hygiene |
| Refactor punch-list A | Absolute broker-config discovery silently relocated `WeftContext.root`; atomic cleanup could mask the primary replace error; numeric schema suffixes accepted noncanonical spellings. | Restored working-directory root ownership for absolute config paths, suppressed cleanup `OSError` only while preserving the primary error, and required canonical ASCII decimal schema suffixes. Added direct RED-to-GREEN probes and clarified [SB-0.4]. | Follow-up outcomes 1-3, [SB-0.4], [MA-1.4] |
| Refactor punch-list B | The Monitor retained a zero-caller raw-delete method/SQL builder, two v5 delete-state columns lacked an explicit v6 decision, and the optional migration extraction needed a deletion-test decision. | Deleted both unused symbols. Kept the two physical columns explicitly as inert/migration-only v6 residue to avoid new cross-backend rewrite code; neither is a live lane and future removal requires a version edge. Declined extraction because it would add an interface without deleting logic or dependencies. | Follow-up outcome 4, [SB-0.4a] |
| Refactor punch-list C | Several owner names/comments/docstrings were imprecise, bundle-root guards diverged, pipeline edge records bypassed the transport encoder, one PONG caller used a literal, and the installed console script lacked a metadata pin. | Renamed the command owner alias, documented report gating, delegated bundle validation to the model owner, encoded complete edge TaskSpecs canonically, used the outbox constant, corrected keyed-reply and regression docstrings, and added the console-script metadata probe. | Follow-up outcome 5, [TS-1], [MF-3], [CLI-0.2] |
| Independent punch-list review | `WeftContext.discovered` still claimed the project root came from upward search, contradicting the restored absolute-config case where cwd remains root. | Corrected the field documentation to describe discovered broker configuration/target ownership. Focused and full gates remained green; final review returned PASS with no remaining finding. | Follow-up outcome 1, [SB-0.4] |
