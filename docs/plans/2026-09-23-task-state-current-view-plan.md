# Task-State Current View and Status/List Projection Plan

Status: draft
Source specs: docs/specifications/00-Quick_Reference.md; docs/specifications/01-Core_Components.md [CC-2.2], [CC-2.4], [CC-2.5]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.6], [OBS.6a], [OBS.13.7]; docs/specifications/10-CLI_Interface.md [CLI-1.2.1], [CLI-1.2.3]; docs/specifications/14-Python_API_Surfaces.md [PY-2]
Superseded by: none

Class: 5 — this changes the runtime-only task-state payload contract and the
authoritative read path used by public status/list surfaces.

Risk triggers: hardened plan required and applied. The work changes a
runtime-state wire payload, crosses task writers, liveness custody, command and
client readers, changes public Python status defaults, and has mixed-version
rollout requirements.

Plan type: implementation with spec revision. Promotion strategy: A — edit the
active spec text before implementation, but add implementation-mapping claims
and reciprocal code backlinks only with the code slice that makes them true.

## 1. Goal

Make `weft.state.tasks.<tid>` the authoritative current-task inventory and the
primary source for ordinary project-wide task snapshots. Slightly expand each
task-owned state row with the minimum current-view fields needed to preserve
the public status/list projection, without copying the full `TaskSpec`, task
input/output, queue configuration, or derived reconciliation data. Default
`weft status` and `weft task list` should list active tasks by enumerating the
task-state namespace and reading the newest valid row from each queue; they
must not replay `weft.log.tasks` or inspect every task-local queue. Historical
`--all`, known-TID detailed inspection, result/control, and event-watch paths
remain explicit exceptions.

The client mirrors the CLI snapshot contract. `Client.system.status()` and
`Client.tasks.list()` default to active tasks, accept the same `status` filter
and `all` inclusion switch, and use the same shared collector as their CLI
counterparts. `Client.tasks.stats()` keeps its separate return type but uses
the same `status`/`all` selection as `weft task list --stats`.

This closes the incomplete read-side migration left by the per-TID task-state
namespace change: TaskMonitor may retire raw lifecycle rows while a task is
still live, but the current global collector still discovers tasks from those
raw rows and only uses task state as enrichment.

## 2. Source Documents

Normative sources to revise and then implement:

- [`docs/specifications/00-Quick_Reference.md`](../specifications/00-Quick_Reference.md):
  task-state and task-log queue summaries.
- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-2.2], [CC-2.4], [CC-2.5]: BaseTask state publication and lifecycle
  reporting ownership.
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-5]: current-state reconstruction, evidence priority, task-log retention,
  and Monitor-store fallback.
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [OBS.6], [OBS.6a], [OBS.13.7]: per-TID state-row validity, publication,
  custody, and conservative liveness.
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.2.1], [CLI-1.2.3]: `status`, `task list`, `task status`, `--all`, and
  watch behavior.
- [`docs/specifications/14-Python_API_Surfaces.md`](../specifications/14-Python_API_Surfaces.md)
  [PY-2]: `TaskSnapshot`, `cmd_status`, and `cmd_task_list` behavior.

Historical context, not normative behavior:

- [`2026-09-11-per-tid-task-state-namespace-plan.md`](./2026-09-11-per-tid-task-state-namespace-plan.md)
  is completed and explains the namespace migration. Its direction remains
  intended, but its status/list slice preserved task-log discovery and is the
  implementation gap corrected here.
- [`2026-05-11-internal-service-observability-plan.md`](./2026-05-11-internal-service-observability-plan.md)
  is completed and records the additive model: internal service tasks may
  appear in both the task projection and the typed service projection.
- [`2026-07-29-task-snapshot-reducer-plan.md`](./2026-07-29-task-snapshot-reducer-plan.md)
  is completed and explains why snapshot reduction is centralized and pure.

Required implementation guidance:

- [`AGENTS.md`](../../AGENTS.md), especially the queue-source-of-truth model,
  layer boundaries, and testing rules.
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md).
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md).
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md).
- [`docs/agent-context/runbooks/adversarial-acceptance-probes.md`](../agent-context/runbooks/adversarial-acceptance-probes.md).

## 3. Context and Key Files

### Current structure

- `weft/core/tasks/base.py::_build_tid_state_payload` currently publishes
  identity and runtime evidence: `full`, `short`, `name`, `role`, `runner`,
  `runtime_handle`, `terminal`, `hostname`, a per-write wall-clock field named
  `started`, and optional `activity`/`waiting_on`. It does not carry exact
  lifecycle status or terminal outcome.
- `weft/core/tasks/base.py::_register_tid_state` appends one complete row to
  the task's own queue. `_report_state_change` publishes lifecycle events to
  `weft.log.tasks`; today it refreshes task state automatically only for the
  terminal transition. Runtime-handle and activity edges publish separately.
- `weft/core/task_state.py` owns namespace discovery and suffix-bound
  newest-valid reads. Its current newest-first read already uses the desired
  common-case operation: one latest-row peek, with pagination only to get past
  malformed tails. Reuse it; do not add a fixed-limit reader.
- `weft/liveness/policy.py` owns the minimal liveness-row decoder and the
  conservative `terminal`/runtime-handle liveness policy. Current-view
  projection validation must not make older rows invalid for liveness.
- `weft/commands/system.py::_collect_task_snapshot_records` currently replays
  all retained `weft.log.tasks` rows to seed TIDs, then enriches only those
  TIDs from task state and task-local/runtime evidence.
- `weft/commands/_task_snapshot_reducer.py` owns pure lifecycle folding and
  `TaskSnapshot` construction. The new state-row projection belongs in this
  pure policy layer rather than in CLI rendering.
- `weft/commands/tasks.py::list_task_snapshots` and
  `weft/commands/system.py::cmd_status` share the global collector. Known-TID
  status, control, result, and watch paths have stronger evidence needs and
  remain separate.
- `weft/client/_namespaces.py::SystemNamespace.status` currently calls
  `system_status()` directly with its old unfiltered Python default. This plan
  changes that public client default to mirror CLI snapshot filtering and adds
  explicit `all` and `status` keyword options.
- `weft/client/_namespaces.py::TasksNamespace.list` and `.stats` already match
  the CLI's active-by-default behavior but expose `include_terminal` instead of
  the CLI's `all` vocabulary. This plan adds `all` as the canonical keyword and
  retains `include_terminal` as a deprecated compatibility alias for one
  release. `include_terminal`, when non-null, supplies the effective value only
  when `all` remains false; combining it with `all=True` is a usage error.
  Removing the alias is a separate public-API decision.
- `weft/core/monitor/task_monitor.py::_build_tid_state_payload` and
  `weft/core/manager.py::_build_tid_state_payload` add bounded task-specific
  fields to the base row. Those extensions must keep working.

### Files expected to change

Specifications and traceability:

- `docs/specifications/00-Quick_Reference.md`
- `docs/specifications/01-Core_Components.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/14-Python_API_Surfaces.md`
- this plan and `docs/plans/README.md`

Production code:

- `weft/core/tasks/base.py`
- `weft/core/task_state.py`
- `weft/liveness/policy.py`
- `weft/commands/_task_snapshot_reducer.py`
- `weft/commands/system.py`
- `weft/commands/tasks.py`
- `weft/client/_namespaces.py`
- `weft/core/manager.py` and `weft/core/monitor/task_monitor.py` only if their
  existing extensions need adaptation to the versioned projection

Tests:

- `tests/tasks/test_task_observability.py`
- `tests/core/test_task_state.py`
- `tests/commands/test_status.py`
- `tests/commands/test_task_commands.py`
- `tests/core/test_client.py`
- focused CLI tests under `tests/cli/` only where command-visible routing or
  JSON behavior is not already covered through the command layer
- `tests/specs/test_plan_metadata.py` and the repository traceability tests as
  gates, not as implementation targets

### Shared paths to reuse; do not duplicate

- `list_task_state_tids`, `read_task_state_snapshot`, and
  `latest_task_state_rows` in `weft/core/task_state.py` remain the only
  broker-aware task-state readers.
- `decode_tid_state_row` and liveness policy remain the minimal row-validity
  boundary. Add a separate current-view projection validator rather than
  tightening liveness validity and breaking mixed-version rows.
- The public `TaskSnapshot` type remains `weft/commands/types.py::TaskSnapshot`.
  Do not create a second public status type.
- Pure conversion and ordering/filtering remain in
  `weft/commands/_task_snapshot_reducer.py`.
- Existing TaskSpec redaction rules must be reused for the metadata field. Do
  not invent a second redaction vocabulary.
- `weft/_constants.py::TASK_LIFECYCLE_STATUS_VALUES` remains the single status
  enumeration used by the writer and validator. Its values do not change in
  this work, so `_constants.py` is an inspected dependency rather than an
  expected edit. Extend the existing equality test in
  `tests/tasks/test_task_observability.py` that binds it to the
  `StateSection.status` Literal.
- Existing historical log folding remains the `--all` compatibility path; do
  not fork a second lifecycle-event reducer.

### Comprehension gate before editing

The implementer must be able to answer these questions from code and specs:

1. Why can a valid nonterminal state row establish membership in the current
   task inventory while still meaning "live, or not yet proven dead" rather
   than positive process proof?
2. Which public snapshot fields can be derived from the task-state message ID
   or current time, and therefore must not be duplicated into the payload?
3. Why must `--all`, known-TID detailed inspection, and `--watch` retain
   separate evidence paths?
4. Why must projection validation not change the liveness-valid definition of
   old rows containing only `full` and `short` plus existing runtime fields?

Stop before implementation if any answer is unclear.

## 4. Payload Budget and Query Contract

### 4.1 Versioned compact projection

Keep the existing top-level liveness/runtime fields and add one nested
`projection` object. The nested boundary prevents public-view fields from
silently becoming inputs to liveness deletion policy and gives mixed-version
readers an explicit capability check.

Base row fields after the change:

| Field | Decision | Reason |
|---|---|---|
| `full`, `short` | keep | suffix binding, identity, and display |
| `name` | keep | public task identity |
| `runner`, `runtime_handle` | keep | liveness, control authority, and public runtime identity |
| `terminal` | keep | existing liveness compatibility; must agree with projected status when projection is valid |
| `role` | keep when present | manager/service/pipeline ownership decisions |
| `hostname` | keep | LivenessMonitor's cross-host probe policy |
| `activity`, `waiting_on` | keep when present and nonterminal | current public activity display |
| `task_monitor` and other already-specified bounded task-type extensions | keep | existing passive diagnostics contract |
| legacy `started` | stop writing in projection-capable rows | it is the row-write wall clock, is not task `started_at`, and has no production reader; the queue message ID is the observation timestamp |

The new `projection` object contains only:

| Field | Presence | Reason |
|---|---|---|
| `version` | always, exact integer `1` | distinguish projection-capable rows from old liveness-only rows |
| `status` | always | filtering and exact current lifecycle label |
| `event` | always | preserve the event column and public JSON value without replaying task logs |
| `started_at` | nullable | public snapshot and duration calculation |
| `completed_at` | terminal/non-null only | terminal public snapshot |
| `return_code` | terminal/non-null only | terminal public snapshot |
| `error` | terminal/non-null only | terminal public snapshot |
| `metadata` | always mapping, redacted | preserve the existing public `TaskSnapshot.metadata` contract without copying the full TaskSpec |

`metadata` is justified only because it is already part of the public snapshot
contract and has firing behavior tests. It must use the existing TaskSpec
redaction paths before publication. Do not store `spec`, `io`, command/function
targets, arguments, limits, tags, input payloads, output/results, or the full
TaskSpec as a shortcut for obtaining metadata.

Do not add these derived or non-current fields:

- `last_timestamp`: use the broker message ID of the selected state row.
- `duration_seconds`: compute from `started_at`, `completed_at`, and the read
  clock.
- `runtime`: it is a read-time runner description, not task-owned current
  state.
- `reconciliation`: it is reducer output, not producer state.
- host/managed/live PID projections: derive them from `runtime_handle` only on
  the existing process-detail path.
- task-local queue evidence, PONGs, claimed outbox results, raw lifecycle-event
  extras, or result values.
- generic runner diagnostics. Terminal/historical diagnostic detail remains on
  the detailed/historical evidence path. Existing explicitly bounded
  task-specific state extensions remain allowed.

Do not copy `pipeline_status` into task state. The current pipeline summary is
owned by the pipeline status queue and is joined by the existing known-TID
inspection path; default bulk status/list does not need it. Do not generalize
task state into an arbitrary `details` or event-extra mapping.

### 4.2 Publication edges

Task-state writes follow current-view changes, not task-log volume.
`_report_state_change(event, **extra)` compares the exact TaskSpec status with
the owner-local last published status and refreshes state only when that status
changed. PAUSE and RESUME are explicit display-state edges even though their
TaskSpec lifecycle status does not change. `_register_tid_state` continues to
publish on runtime identity, activity/waiting, and already-specified bounded
task diagnostic changes. BaseTask retains the last display event in
owner-local memory so runtime-only and diagnostic-only refreshes carry it
forward. Activity publication records `task_activity`; PAUSE/RESUME record
their control display events. Operational reports such as `poll_report`,
`output_spilled`, manager child events, and recurring heartbeats stay in the
task log and do not cause state writes solely because they were reported.

Avoid duplicate initial and terminal state rows introduced solely by the
refactor. Preserve initialization ordering by setting the owner-local display
event to `task_initialized`, performing the existing eager state publication,
and marking the matching `_report_state_change` call as already published;
do not move first state visibility behind endpoint setup merely to remove a
write. Later status, display, activity, runtime, and bounded-diagnostic edges
publish exactly once through the same `_register_tid_state` path. Preserve the
terminal-success latch and best-effort failure policy: state publication
failure must not rewrite lifecycle state or abort task execution. The failure
latch starts armed, so the first failure in each consecutive run emits one
identifier-only warning; repeated failures remain debug-level until a success
re-arms the warning latch. A later
owner edge retries by publishing a complete row. Do not introduce writer-side
history scans, equivalence checks, or read-before-write deduplication.

### 4.3 Read-path matrix

| Surface | Task inventory/source after the change | Exceptions/notes |
|---|---|---|
| `weft status` snapshot, default | enumerate canonical task-state queues; newest valid row per TID; include nonterminal projection rows | manager, broker, and typed service panels retain their own sources |
| `weft status --status <nonterminal>` | same state-only current projection, then filter | no global task-log replay |
| `weft task list`, `--status <nonterminal>`, `--stats` | same shared state-only current projection | no second collector in `tasks.py` |
| `status --all` and `task list --all` | current task-state projection union existing retained-log historical reconstruction | `--all` continues to mean "include visible terminal tasks," not durable complete history; deduplicate by full TID |
| old-format newest nonterminal state row with no `projection` key | valid current-inventory candidate; use one compatibility log replay for the call, then emit a degraded `unknown` snapshot if no reconstructible row exists | mixed-version compatibility only; never use an older projection behind a newer valid liveness row |
| newest nonterminal row with a present but invalid/unsupported `projection` | emit a degraded `unknown` snapshot directly from safe top-level identity/runtime fields | do not replay the task log and mask a writer/schema defect |
| newest state row with `terminal: true` | excluded from default active views; eligible in `--all` with terminal log evidence preferred when present | state `terminal` alone does not replace terminal evidence priority for known-TID/result paths |
| `task status TID`, `--ping`, `--process` | keep the known-TID evidence path | may inspect task-local control/outbox/runtime and terminal Monitor-store fallback |
| `status --watch` and task watch/events | keep `weft.log.tasks` event streaming | snapshots must not be emulated by polling events, and event streams must not be emulated from state history |
| result and stop/kill | unchanged | retain shared task-evidence and control contracts |
| `Client.system.status()` | same collector and default filtering as CLI snapshot status; `all=False`, optional `status` | `all=True` includes visible terminal tasks and stopped managers exactly as CLI `--all`; client watch/event APIs remain separate |
| `Client.tasks.list()` / `.stats()` | same current collector and `status`/`all` selection as `weft task list` / `--stats` | non-null `include_terminal` is a one-release deprecated alias used only when `all=False`; combining it with `all=True` is a typed usage error |

Compatibility resolution happens before the `--all`/`all=True` union: an
absent-projection candidate may become the reconstructible log snapshot, or an
unavailable-projection degraded snapshot if reconstruction fails. The union
then has an exact precedence rule. Terminal lifecycle proof from the retained-
log fold outranks any current-state hint for the same TID. Otherwise the
selected current-state snapshot, including a degraded projection snapshot,
outranks a nonterminal log-folded record. Log-only records retain their
existing inclusion behavior. Therefore an invalid projection never causes
replay and cannot be masked by nonterminal history; `--all` may still scan
history independently, and genuine terminal proof may replace the degraded row
under the existing terminal-evidence priority.

The common state-only path must use one shared broker scope, enumerate the
namespace once, and call the existing newest-valid bulk reader. It must not
open `weft.log.tasks`, outboxes, or control queues. A state namespace/read
backend error fails the command through the existing typed command boundary;
it must not silently return an empty project.

## 5. Invariants and Constraints

- TIDs remain immutable canonical 19-digit task IDs. Namespace suffix binding
  remains mandatory.
- Task lifecycle transitions remain forward-only. A projected `status` cannot
  move a previously observed task backward.
- `terminal` remains the liveness-policy compatibility field and must equal
  `status in TERMINAL_TASK_STATUSES` for a projection-capable row. A mismatch
  invalidates the projection, not the underlying liveness row.
- A valid nonterminal state row means current inventory membership under the
  existing conservative rule: live, or not yet proven dead. It does not claim
  positive PID/container liveness.
- LivenessMonitor remains the sole deleter of `weft.state.tasks.<tid>` rows.
  This plan does not change retirement timing, probe authority, or cleanup.
- Names-only namespace discovery still proves neither payload validity nor
  liveness. Invalid-only queues remain short-ID candidates but do not become
  task snapshots.
- The newest liveness-valid row is authoritative. Never search behind it for
  an older projection-capable row, because doing so can resurrect stale
  status. Pagination behind malformed rows remains required.
- State queues remain runtime-only and excluded from dump/load persistence.
- `spec` and `io` immutability and redaction remain intact. No full TaskSpec is
  copied into task state.
- Status/list public dataclass and JSON field names remain stable. Fields that
  require detailed evidence may remain `None` on the bulk current-view path
  only where the existing contract already marks them optional.
- Internal services remain additive: a service task may appear once under
  `tasks` and once under `services`. Do not "fix" duplication by filtering it
  out of the task inventory.
- Existing bounded TaskMonitor state diagnostics and manager structural role
  survive the base-row change.
- Live pipeline detail remains on the existing pipeline status queue and
  known-TID join path; it is not copied into the base task-state projection.
- Default bulk projection must not actively PING tasks, read task-local
  outboxes/control queues, or scan the global lifecycle log.
- No new dependency, database, cache, background worker, queue, or public CLI
  flag is introduced. The public client gains CLI-shaped `all`/`status`
  options; the existing `include_terminal` client keyword remains a deprecated
  compatibility alias for one release.
- No second snapshot reducer. State rows and log-folded records converge on
  the existing `TaskSnapshot` projection and ordering/filtering policy.
- Real broker/task tests are required. Do not replace namespace, latest-row,
  TaskMonitor deletion, or lifecycle publication proofs with mocks.

### Error priorities

- Invalid projection data is a degraded observability condition. It must not
  mutate/delete the row or kill a running task.
- Broker acquisition/read failure is a command failure, not an empty result.
- State publication remains best effort and nonfatal to execution. The first
  consecutive failure is warning-visible using identifiers only; repeated
  failures are debug-level until a success re-arms the latch. A successful
  later owner edge republishes the whole current view.
- Terminal lifecycle/result truth continues to outrank current-state hints in
  the detailed evidence path.

### Stop-and-re-plan gates

Stop and return for design review if implementation requires any of these:

- storing the full TaskSpec or arbitrary event payloads in task state;
- changing LivenessMonitor deletion or unknown-deadline policy;
- treating Monitor nonterminal collation as live evidence;
- changing `TaskSnapshot` public field names or command signatures beyond the
  explicitly planned client mirror: `Client.system.status(*, all=False,
  status=None)`, `Client.tasks.list(*, status=None, all=False,
  include_terminal=None)`, and `Client.tasks.stats(*, status=None, all=False,
  include_terminal=None)`, with the one-release compatibility alias on the
  latter two;
- adding per-task global-log scans or fixed-limit history reads;
- making task-state publication fatal to task execution;
- a second task-list/status reducer or a cache outside queue state;
- removing historical/detailed evidence paths to make the state-only path fit.

## 6. Spec Baseline

- `cfa5bb2174fe101ecb26e435e05f2e83ec983336` —
  `docs/specifications/00-Quick_Reference.md`,
  `01-Core_Components.md`, `05-Message_Flow_and_State.md`,
  `07-System_Invariants.md`, `10-CLI_Interface.md`, and
  `14-Python_API_Surfaces.md` at plan authoring time.
- Plan type: implementation with spec revision.
- Promotion baseline: pending the spec-promotion slice. Record the resulting
  commit SHA, or the baseline SHA plus exact spec diff when implementation is
  intentionally reviewed uncommitted, before code work begins.

## 7. Proposed Spec Delta

Promotion strategy A applies to each block below: land the requirement text
first without new implementation-mapping claims. Add mappings and reciprocal
code docstrings only when the implementation slice lands.

| Spec file | Strategy | Sections touched |
|---|---|---|
| `00-Quick_Reference.md` | A | task-state and task-log notes |
| `01-Core_Components.md` | A | [CC-2.2], [CC-2.4], [CC-2.5] |
| `05-Message_Flow_and_State.md` | A | [MF-5] current reconstruction/evidence |
| `07-System_Invariants.md` | A | [OBS.6], [OBS.6a], [OBS.13.7] |
| `10-CLI_Interface.md` | A | [CLI-1.2.1], [CLI-1.2.3] |
| `14-Python_API_Surfaces.md` | A | [PY-2] status/list snapshot semantics |

### [OBS.6] — append after the current row-validity paragraph

> A task-state row may carry a versioned `projection` object for bulk current
> status/list reads. Projection version 1 contains exactly `version`, `status`,
> `event`, `started_at`, and redacted public `metadata`, plus non-null terminal
> `completed_at`, `return_code`, and `error` values when present. Identity,
> runtime authority, `terminal`, role, hostname, and current activity remain
> top-level runtime-state fields. The selected row's broker message ID is its
> observation timestamp; duration and reconciliation remain reader-derived.
> The projection never embeds the full TaskSpec, spec/io sections, task input or
> output, result values, queue configuration, or arbitrary lifecycle-event
> extras. Existing bounded task-type extensions remain additive.
>
> Liveness validity and projection validity are separate. An older row without
> `projection`, or a row with an invalid projection, may remain valid liveness
> evidence under the existing minimum shape. A valid projection requires its
> top-level `terminal` flag to agree with its exact status. Readers never skip a
> newer liveness-valid row to revive an older projection-capable row.
>
> Projection version 1 has the exact required keys `version`, `status`,
> `event`, `started_at`, and `metadata`; its only optional keys are
> `completed_at`, `return_code`, and `error`. Unknown keys are invalid.
> `version` is the exact integer `1` and a boolean is not an integer for this
> contract. `status` is exactly one of `TASK_LIFECYCLE_STATUS_VALUES`, whose
> equality with the `StateSection.status` Literal remains a firing test.
> `event` is a nonblank string. `started_at` is null or a positive integer
> excluding booleans. `metadata` is a JSON object. Optional timestamps and
> return codes are integers excluding booleans; `completed_at`, when present,
> is positive and not earlier than `started_at`; `error`, when present, is a
> nonblank string. Optional terminal fields are omitted rather than stored as
> null, and they are invalid on nonterminal projections. If a terminal row has
> a non-null TaskSpec terminal field, the projection carries it.

### [OBS.6a] — append to the publication-edge rules

> Task state publishes only on a current-view edge: eager initialization, an
> exact lifecycle-status change, an activity/waiting change, PAUSE/RESUME
> display-state change, runtime identity change, or an existing bounded
> task-specific diagnostic change. A task-log report such as `poll_report`,
> `output_spilled`, a manager child event, or recurring heartbeat emission does
> not publish task state merely because it was logged. Status transitions set
> the projection event to their reporting event; `task_activity` and
> `control_pause`/`control_resume` update the display event; runtime-only and
> diagnostic-only refreshes carry the last display event forward. Publication
> does not read history or copy arbitrary event extras. Initial and terminal
> paths avoid duplicate writes introduced solely by this projection change,
> retain the terminal-success latch, and keep task-state broker failures best
> effort and nonfatal.

### [OBS.13.7] — append to current-state custody

> The newest valid nonterminal task-state row is the authoritative membership
> source for bulk current-task inventory. This remains conservative membership
> evidence, not proof of a currently observable process. LivenessMonitor
> retirement removes dead membership; task-log retention does not remove a
> task from the current inventory.

### [MF-5] — replace the bulk status/list reconstruction paragraph

> Default project-wide status and task-list snapshots enumerate canonical
> `weft.state.tasks.<tid>` queues and read the newest liveness-valid row from
> each queue in one broker scope. Projection-capable nonterminal rows directly
> supply the current task projection. The common path does not replay
> `weft.log.tasks`, inspect every task-local outbox/control queue, or actively
> PING tasks. A newest nonterminal liveness-valid row with no `projection` key
> is treated as an old-writer compatibility case and triggers one lifecycle-log
> replay for the call; if no record can be reconstructed, the task remains
> visible as a degraded `unknown` snapshot. A present but invalid or unsupported
> projection never triggers that replay: it produces a degraded `unknown`
> snapshot so a current writer/schema defect cannot masquerade as healthy
> state.
>
> Historical inclusion (`--all`), known-full-TID detailed inspection,
> result/control reconciliation, and event watching retain their specialized
> evidence paths. `--all` unions current task-state snapshots with the existing
> retained lifecycle-log reconstruction and deduplicates by full TID; it means
> include terminal tasks still visible to current operational evidence, not an
> indefinitely retained audit inventory. During deduplication, terminal
> lifecycle proof from the retained-log fold outranks the current-state row;
> otherwise the selected current-state snapshot, including an invalid-
> projection degraded snapshot, outranks a nonterminal log-folded record.
> Thus an invalid projection never causes replay or gets masked by nonterminal
> history, although `--all` scans history independently. Known-TID terminal
> Monitor-store fallback and the shared terminal evidence priority are
> unchanged.

### [CLI-1.2.1] — replace the task-source sentence and add the exception rule

> Snapshot-mode project status obtains its task list from the newest valid rows
> in `weft.state.tasks.*`; manager, broker, and typed service panels retain
> their existing sources. Default and nonterminal-filtered snapshots do not
> replay the global task log. `--all` adds the existing retained terminal
> lifecycle reconstruction. `--watch` remains an event stream over lifecycle
> evidence and does not poll task-state history.

### [CLI-1.2.3] — append to `task list` behavior

> Default `task list`, nonterminal status filters, and `--stats` share the same
> newest-task-state current projection as project status. `--all` additionally
> scans retained historical lifecycle evidence and deduplicates by TID. A live
> internal-service TID may appear in both the task list and the typed Services
> section; the service projection is additive, not a filter over tasks.

### [PY-2] — replace the unfiltered-Python-default sentence and append public snapshot behavior

> `cmd_status` and `cmd_task_list` share one bulk current-task collector backed
> by newest valid per-TID task-state rows. Their public `TaskSnapshot` field
> names remain unchanged. `last_timestamp` comes from the selected broker row,
> duration is derived, and optional detailed-evidence fields remain absent when
> the bulk current-view path does not acquire that evidence. Known-TID status,
> control/result helpers, and streams retain their documented stronger paths.
> `Client.system.status(*, all=False, status=None)` mirrors CLI snapshot status:
> the default excludes terminal tasks, `status` applies the same task-status
> filter, and `all=True` includes visible terminal tasks and stopped managers.
> It delegates to the same resolved-context system-status capability as the
> command adapter. `Client.tasks.list(*, status=None, all=False,
> include_terminal=None)` and `Client.tasks.stats(*, status=None, all=False,
> include_terminal=None)` use the same selection as
> `weft task list` and `weft task list --stats`. For one release their existing
> keyword-only `include_terminal` argument remains a deprecated alias for
> `all`. A non-null alias supplies the effective value only while `all=False`;
> combining it with `all=True` receives the existing typed usage error rather
> than precedence chosen silently. Python event/watch APIs remain separate and
> keep their streaming contracts.

### [CC-2.2] — append to BaseTask responsibilities

> BaseTask publishes the compact versioned current-view projection defined by
> [OBS.6] through its existing task-state builder and append path. It stores
> only public metadata after applying the same configured TaskSpec redaction
> paths used by lifecycle logs; it never embeds the full TaskSpec. Owner-local
> status/activity/runtime edge detection decides when to append, and task-type
> overrides may add only their already-specified bounded diagnostics.

### [CC-2.4] — append after the current `terminal` mapping rule

> A projection-capable task-state row carries the exact current TaskSpec status
> and a last display event. The top-level `terminal` hint agrees with that
> status. Lifecycle-status, activity/waiting, and PAUSE/RESUME display changes
> refresh the projection; logging an operational report without a current-view
> change does not. Public activity remains derived, and the projection does not
> create a second lifecycle state machine.

### [CC-2.5] — append to task-state publication flow

> Startup seeds `task_initialized` and performs the existing eager state append
> before endpoint setup, then emits the matching lifecycle event without a
> duplicate state row. Later current-view edges append once through the same
> BaseTask path. The task-state failure-warning latch starts armed. The first
> append failure in each consecutive failure run emits one identifier-only
> warning containing TID, projected status, and display event; repeated
> failures remain debug-level until a successful append re-arms the warning
> latch. Publication remains nonfatal and a later owner edge retries a complete
> snapshot.

### Quick Reference queue note and redaction setting

Add concise text, consistent with the exact rules above:

> `weft.state.tasks.<tid>` is the current live-task inventory and carries a
> compact versioned status/list projection. `weft.log.tasks` remains retained
> lifecycle/event evidence for history, detailed reconciliation, result, and
> watch paths; deleting raw log rows does not remove a live task from default
> status/list output.

Replace the `WEFT_REDACT_TASKSPEC_FIELDS` description with:

> Comma-separated TaskSpec field paths redacted from task-log events and the
> public metadata copied into versioned task-state status/list projections.

### [PY-2] — append the degraded snapshot shape

The command reducer contract for a newest nonterminal row without a usable
projection is exact:

- keep suffix-bound `tid`/derived short ID;
- use a nonblank top-level `name` when valid, otherwise the full TID;
- set `status="unknown"`;
- set `event="task_state_projection_unavailable"` for an absent old-format
  projection, or `event="task_state_projection_invalid"` for a present invalid
  or unsupported projection;
- retain a structurally valid top-level runner name/runtime handle, activity,
  and waiting target; otherwise set those fields to null;
- set lifecycle timestamps, return code, error, duration, runtime description,
  pipeline status, and runner diagnostics to null;
- set metadata to an empty mapping because an invalid projection cannot supply
  trusted public metadata;
- set `last_timestamp` to the selected task-state message ID; and
- attach exactly
  `{"classification": <classification>, "reason": <reason>, "observed_at": <message-id>}`.

For an absent projection, `classification` is
`task_state_projection_unavailable` and `reason` is `projection_missing`. For a
present invalid/unsupported projection, `classification` is
`task_state_projection_invalid` and `reason` is the first failure in this
deterministic validation order:

1. projection object type: `projection_not_mapping`;
2. missing required keys: `projection_missing_key`;
3. unknown keys: `projection_unknown_key`;
4. version exact-int type: `projection_version_type`;
5. unsupported version value: `projection_version_unsupported`;
6. lifecycle status: `projection_status`;
7. top-level terminal agreement: `projection_terminal_mismatch`;
8. display event: `projection_event`;
9. start timestamp: `projection_started_at`;
10. metadata object: `projection_metadata`;
11. completed timestamp: `projection_completed_at`;
12. return code: `projection_return_code`;
13. error string: `projection_error`;
14. terminal-only optional fields on a nonterminal row:
    `projection_terminal_field_on_nonterminal`; and
15. completed-before-started ordering: `projection_timestamp_order`.

The reducer exposes only these bounded codes, never field contents, exception
text, or arbitrary validator messages. Tests cover every code and prove the
first-failure order for payloads with more than one defect.

## 8. Rollout, Compatibility, and Rollback

This is an additive runtime-only row migration, not a persisted database
migration or queue rename.

Rollout order:

1. Promote the spec text and record the promotion baseline.
2. Ship writer support first. Old readers ignore the nested `projection` and
   continue using task logs.
3. Ship projection validation and pure row-to-snapshot conversion.
4. Switch default bulk readers. During mixed processes, a newest old-format
   row with no projection invokes the one-per-call compatibility replay instead
   of disappearing. A present invalid or unsupported projection is degraded
   directly and never hidden by log replay.
5. Retain historical and known-TID paths until all acceptance tests prove the
   narrower routing boundaries.

Rollback:

- Reverting readers is safe because writers only add a nested field and old
  liveness validation ignores it.
- Reverting writers is safe because new readers recognize missing projection
  capability and use compatibility reconstruction.
- Do not remove compatibility fallback in this plan. Runtime-only rows age out
  under existing LivenessMonitor custody, so no migration or destructive
  rewrite is required.
- The legacy `started` field may disappear from new projection-capable rows
  because no production reader uses it. Rollback readers must not depend on
  it; verify this with repository search and tests before the writer slice.

The main residual risk is a task-state publication failure after all retries:
because task state is the authoritative live inventory, that task may be
temporarily absent until its next lifecycle/activity/runtime publication.
This plan does not make observability failure fatal to execution. If testing
shows a quiet task can complete useful work without any successful
projection-capable publication after initialization, stop and raise a separate
reliability decision rather than silently restoring global log discovery.

## 9. Implementation Tasks

1. **Promote the current-view contract before code changes.**
   - Outcome: the exact delta in section 7 becomes normative and this plan is
     linked from each touched spec's Plans/Related Plans section.
   - Files: the six specs listed in section 7, this plan's promotion-baseline
     line, and nearby implementation notes only where wording is already stale.
   - Preserve strategy A: do not claim new code ownership yet.
   - Verify:
     - `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q`
     - run the exact backstitch command from section 11 and confirm no new
       error/warning is keyed to a promoted section, this plan, or a touched
       implementation path. The repository-wide diagnostic baseline is not
       clean, regardless of the command's process exit status.
   - Stop if the spec delta would require changing public field names,
     LivenessMonitor policy, or TaskSnapshot metadata semantics.

2. **Write red tests for the compact state projection and publication edges.**
   - Outcome: tests define the exact base payload budget and fail because the
     projection is not yet published.
   - Files: `tests/tasks/test_task_observability.py`, with focused additions in
     existing manager/monitor tests only for their state-row extensions.
   - Use real task objects and broker-backed queues. Assert:
     - exact base top-level keys plus explicitly allowed task-type extensions;
     - exact `projection` keys for nonterminal and terminal rows;
     - projected status accepts exactly the existing
       `TASK_LIFECYCLE_STATUS_VALUES`, and its pre-existing equality with the
       `StateSection.status` Literal remains a firing enumerable-contract test;
     - redacted metadata is public-equivalent but full `taskspec`, `spec`, `io`,
       targets, inputs, outputs, results, and arbitrary event extras are absent;
     - `terminal` agrees with projected status;
     - initialization, spawning/running, activity, runtime-handle, and terminal
       edges leave a self-contained newest row;
     - direct runtime refresh preserves the last display event;
     - PAUSE and RESUME refresh the display event without inventing a lifecycle
       transition;
     - `poll_report`, `output_spilled`, manager child reports, and recurring
       heartbeat logs do not append state when no current-view field changed;
     - the legacy `started` write-clock field is absent from new rows;
     - specialized manager role and bounded TaskMonitor diagnostics remain.
   - Red-green requirement: capture the focused failing test output before
     implementing task 3.
   - Stop if preserving public metadata requires storing the full TaskSpec or
     duplicating redaction logic.

3. **Implement one compact BaseTask publisher.**
   - Outcome: every current-view owner edge writes the complete version-1
     projection with no duplicate publication path or report-driven write
     amplification.
   - Files: `weft/core/tasks/base.py`; adapt `weft/core/manager.py` and
     `weft/core/monitor/task_monitor.py` only for their existing bounded
     extensions. Pipeline status remains on its separate status queue and does
     not require a pipeline writer change.
   - Reuse `_build_tid_state_payload`, `_register_tid_state`, existing TaskSpec
     redaction, and terminal publication latch. Keep all imports at module top.
   - Add only the owner-local last-display-event, last-published-status, and
     consecutive-publication-failure latch needed for exact edge detection,
     complete runtime-only refreshes, and bounded warning behavior. Do not add
     a generic event-extra cache or event-name allowlist.
   - Preserve the existing eager-initial-publication order explicitly: seed
     `task_initialized`, publish once, then let the matching lifecycle-log call
     know state is already current. Do not solve duplication by delaying first
     state publication until after endpoint setup.
   - Make `_report_state_change` compare exact lifecycle status and publish only
     after a status mutation. Mark PAUSE/RESUME explicitly at their control
     owner rather than inferring state semantics from arbitrary event names.
     Keep the state append nonfatal and independent from the task-log write.
   - Warn once per consecutive task-state failure run using only TID, projected
     status, and display event; keep repeats at debug and re-arm the latch on a
     successful append.
   - Verify the focused task-observability and service tests, plus the existing
     pipeline-detail regression proving it remains a known-TID join.
   - Stop if the refactor creates two writers, reads state history before
     publishing, or changes lifecycle transition order.

4. **Add projection validation and pure row-to-snapshot reduction.**
   - Outcome: commands can distinguish a liveness-valid row from a
     projection-capable row and build the existing public snapshot without a
     TaskSpec-bearing log record.
   - Files: `weft/core/task_state.py`, `weft/liveness/policy.py`,
     `weft/commands/_task_snapshot_reducer.py`, `tests/core/test_task_state.py`,
     and pure reducer tests near existing snapshot tests. Import the existing
     status set from `weft/_constants.py`; do not duplicate or change it.
   - Preserve minimal liveness decoding. Projection validation enforces the
     exact version-1 key set and value types from section 7, including
     bool-excluding integer checks, the shared
     `TASK_LIFECYCLE_STATUS_VALUES` set, timestamp ordering, terminal-only
     outcome fields, and status/`terminal` agreement. It does not delete or
     invalidate the liveness row.
   - The pure reducer derives `last_timestamp` from the selected message ID and
     duration from lifecycle timestamps. It must produce the existing
     `TaskSnapshot`; do not add a parallel public type.
   - Test malformed tails, old rows, every invalid key/type/value class,
     unsupported versions, terminal mismatch, metadata redaction shape, and
     ordering. The newest liveness-valid row must block fallback to an older
     projected row. An absent projection is the only compatibility-log case;
     a present invalid projection directly produces the exact degraded shape.
     Assert the complete reconciliation mapping, every stable reason code, and
     deterministic first-failure selection.
   - Stop if validation requires a fixed history limit or if commands start
     importing task implementation classes.

5. **Route default bulk status/list through task state.**
   - Outcome: the task portion of default `status` and `task list` performs one
     namespace enumeration and newest-valid read per candidate, with no global
     task-log or task-local queue reads.
   - Files: `weft/commands/system.py`, `weft/commands/tasks.py`,
     `weft/client/_namespaces.py`, `tests/commands/test_status.py`,
     `tests/commands/test_task_commands.py`, and `tests/core/test_client.py`.
   - Add one shared resolved-context current collector in `system.py`; CLI
     adapters, `Client.system.status()`, `Client.tasks.list()`, and
     `Client.tasks.stats()` use it rather than calling each other. Keep the
     existing log-fold collector as the historical/detailed compatibility
     helper.
   - Give the internal `system_status` collection boundary an explicit
     terminal-task inclusion input instead of always collecting terminal tasks
     and filtering them only after the expensive read. Keep stopped-manager
     inclusion separate; `--all` continues to request both behaviors at the
     command boundary.
   - Default/nonterminal paths select nonterminal projected rows. `--all`
     unions the current projection with the existing retained-log collector,
     deduplicates by TID, and applies the exact precedence rule: terminal log
     proof wins; otherwise current state, including a degraded row, wins over
     nonterminal log history.
   - If any newest liveness-valid row has no `projection` key, run the legacy
     global fold once for the whole call, not once per task. If it still cannot
     reconstruct that live candidate, emit the exact unavailable-projection
     degraded snapshot rather than hiding it. A present invalid/unsupported
     projection never opens the log and directly emits the distinct invalid
     degraded snapshot.
   - Adapt typed service projection to use explicit state role/service metadata
     rather than requiring a full TaskSpec-bearing log row. Preserve additive
     Tasks/Services membership and all three built-in service keys.
   - Preserve ordering and status filters. Use the implementable compatibility
     signature `include_terminal: bool | None = None`. Make `all` canonical;
     when the alias is non-null and `all=False`, its true or false value is the
     effective inclusion switch. Raise `CommandUsageError` when `all=True` is
     combined with either non-null alias value. No sentinel is needed because
     the plan deliberately does not reject explicit `all=False` plus the
     compatibility alias.
   - Add an architectural test that makes opening/iterating `weft.log.tasks`,
     outbox, or control queues fail on the default path while real task-state
     rows remain readable. This internal I/O assertion supports, but does not
     replace, the public broker-backed regression.
   - Stop if `tasks.py` grows a second collector or if state-only listing starts
     actively probing every task.

6. **Prove cleanup independence and mixed-version compatibility end to end.**
   - Outcome: live user tasks and all internal service tasks remain visible
     after TaskMonitor deletes their raw lifecycle rows.
   - Use `WeftTestHarness` and the real broker/manager/task lifecycle. Do not
     mock TaskMonitor deletion or namespace reads.
   - Required regressions:
     - start a quiet user task and the manager services;
     - wait for projection-capable task-state rows;
     - run the default destructive TaskMonitor pass until their raw task-log
       rows are gone;
     - assert default `weft status` and `weft task list` still contain every
       active TID with correct status/name/activity;
     - assert the same service TID may appear once in tasks and once in
       services;
     - assert an old-format live row triggers one compatibility replay;
     - assert a projection-invalid live row is degraded, not hidden;
     - assert a projection-invalid row alone does not open the lifecycle log;
     - assert `all=True` does not replace that degraded row with a nonterminal
       log record, but does replace it when the log contains terminal proof;
     - assert an invalid-only namespace queue is not listed as a task;
     - assert a terminal state row is hidden by default and included by
       `--all` while visible evidence remains;
     - assert watch, known-TID status, result, stop, and kill still use their
       stronger existing paths;
     - assert CLI and client system status return the same task and manager sets
       for default, `status=...`, and `all=True` snapshots;
     - assert CLI task list and client task list/stats use the same default,
       filtered, and all-visible-terminal task sets, including alias-conflict
       error behavior;
     - assert alias `True` and `False` values work when `all=False`, canonical
       `all` works without the alias, and `all=True` with either non-null alias
       value raises `CommandUsageError`.
   - Add operation-count or fail-if-open evidence showing the successful
     all-new default path never reads the global task log.
   - Stop if timing sleeps become correctness conditions; use harness waits and
     exact queue evidence.

7. **Reconcile traceability and close the implementation.**
   - Update implementation mappings in the touched specs and reciprocal module
     or owner-function docstrings in the same slice.
   - Record the promotion baseline and any deviations. No `pending` deviation
     may remain at completion.
   - Update README/CLI wording only where it claims task-log-based current
     enumeration; do not perform unrelated documentation cleanup.
   - Run the full verification gates in section 11, then an independent
     completed-work review.
   - Mark the plan `completed` and update the index only after the work is
     committed and `git log` proves the landing, per the repository handoff
     gate. Until then the plan remains `draft`.

## 10. Testing Plan

### Red-green tests

- Exact compact projection shape and absence of forbidden fields.
- Lifecycle/activity/runtime edges update the newest projection.
- Operational task-log reports without a current-view change do not append a
  task-state row.
- Default global list/status survives deletion of raw task-log rows.
- Default global list/status fails the test if it tries to scan the task log.
- Mixed-version newest rows use compatibility without stale projection
  resurrection.
- Degraded reconciliation uses the exact three-key mapping and stable reason
  code selected by deterministic validator order.

### Contract tests

- Public `TaskSnapshot` field names and JSON types remain unchanged.
- Redacted metadata remains equivalent to the current public snapshot
  contract; secrets covered by TaskSpec redaction never appear in task state.
- Status filtering, ordering, `--stats`, and default terminal exclusion remain
  unchanged.
- Client system status and task list/stats select the same rows as the
  equivalent CLI snapshots; the compatibility alias cannot silently override
  `all=True`.
- Client task alias `True`/`False` values, canonical `all`, and both
  `all=True` conflict cases have firing tests with `CommandUsageError`.
- `--all` continues to mean include visible terminal tasks, not durable audit
  history.
- `all=True` preserves a degraded current row over nonterminal log history;
  terminal log proof alone replaces it.
- Internal service duplication across Tasks and Services is intentional and
  deterministic.
- Backend/read failures become typed command failures with no traceback.

### Real-path requirements

- Keep queue reads, task lifecycle, manager services, and TaskMonitor cleanup
  real through `WeftTestHarness` or real broker fixtures.
- Mock only external runner SDK description calls or process visibility where
  deterministic host behavior cannot be arranged.
- Do not use a hand-built dict alone as proof that BaseTask publishes the
  correct row; at least one test must inspect the actual task-owned queue.
- Do not use sleeps as the sole proof of monitor deletion or publication.

### Edge cases deliberately included

- malformed newest rows followed by an older valid row;
- newest valid liveness row with no projection;
- newest valid liveness row with malformed/mismatched projection;
- present invalid/unsupported projection without any lifecycle-log read;
- state row written by an old process during mixed rollout;
- task-state write failure followed by a later successful owner edge;
- terminal task state remaining briefly before LivenessMonitor retirement;
- all three internal services and a quiet ordinary user task;
- task-specific bounded extensions and redaction.

## 11. Verification and Gates

Per-slice commands, using the repository-managed environment:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/tasks/test_task_observability.py -q
./.venv/bin/python -m pytest tests/core/test_task_state.py -q
./.venv/bin/python -m pytest tests/commands/test_status.py -q
./.venv/bin/python -m pytest tests/commands/test_task_commands.py -q
./.venv/bin/python -m pytest tests/core/test_client.py -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
```

Final gates:

```bash
. ./.envrc
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
```

Run the exact traceability command before spec promotion and after final
reconciliation:

```bash
../backstitch/.venv/bin/backstitch check \
  --repo-root /Users/van/Developer/weft \
  --no-config \
  --spec-root docs/specifications \
  --plan-root docs/plans \
  --code-root weft \
  --code-root tests \
  --code-root bin \
  --code-root integrations \
  --code-root extensions \
  --format json \
  --output /tmp/weft-task-state-current-view-backstitch.json
```

The plan-authoring snapshot, including this draft, reports 375 spec sections,
1,553 code refs, 1,020 mappings, 30 errors, 1,158 warnings, and 686 infos. The
final recorded invocation exits 1 because the report contains existing
repository debt. Completion does not
require aggregate-count equality because the planned mappings intentionally
change counts. It requires a severity/code/path/message comparison showing no
new error or warning tied to this plan, the promoted sections, or touched code,
and no reciprocal backlink debt for the implemented ownership claims.

Run focused black-box CLI probes for default `status`, default `task list`,
`task list --all`, malformed/old task-state rows, and backend-read failure.
Every failure must have the documented exit-code class and no traceback.

Observable runtime success:

- after a destructive TaskMonitor pass removes active-task lifecycle rows,
  default status/list still show the task from its newest state row;
- the all-new common path records no global task-log/task-local reads;
- default output latency/work scales with retained current task-state queues,
  not total retained lifecycle history;
- known-TID details, result, control, and watch behavior remain unchanged.

## 12. Independent Review Loop

This plan crosses task publication, liveness state, monitor cleanup, public
commands, and six specs. External review is mandatory.

Reviewer instructions:

> Read this plan, especially sections 4 and 7, at baseline
> `cfa5bb2174fe101ecb26e435e05f2e83ec983336`. Inspect BaseTask publication,
> task-state decoding/liveness policy, status/list collection, TaskMonitor
> cleanup, and the governing spec sections. Look for missing fields, accidental
> TaskSpec duplication, stale-row resurrection, mixed-version failures,
> retained-log dependencies on the claimed state-only path, or degradation of
> known-TID/result/control/watch behavior. Prefer removing unnecessary stored
> fields or work. Answer PASS or BLOCKED: could you implement this plan
> confidently and correctly, and would it preserve or improve robustness?

The author must reproduce each finding, record its disposition in the Review
Log, and request a scoped second pass over accepted fixes. A completed-work
review is required again after the implementation slices.

## 13. Out of Scope

- Making `--all` a durable or complete audit inventory.
- Adding global enumeration over Monitor-store terminal history.
- Changing result, stop/kill, PING/PONG, known-TID detailed inspection, or
  watch evidence precedence.
- Changing LivenessMonitor probe, timeout, deletion, or restart behavior.
- Persisting `weft.state.*` through dump/load.
- Removing `weft.log.tasks` or Monitor collation.
- Adding a generic plugin/status-details framework.
- Moving pipeline status snapshots out of their existing status queue or
  copying them into every task-state row.
- Renaming queues, public CLI flags, public dataclass fields, or TaskSpec
  fields.
- Performance work beyond removing global lifecycle replay and task-local
  reads from the common status/list path.

## 14. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|

## 15. Review Log

| Date | Reviewer | Verdict | Findings and disposition |
|---|---|---|---|
| 2026-09-23 | Plan author fresh-eyes review | PASS after revision | Clarified eager initial publication without duplicate rows; limited degraded compatibility to nonterminal rows; kept pipeline detail on its existing known-TID queue path; required an explicit terminal-task collection input instead of post-read filtering. Residual risk: repeated redacted public metadata increases row size, but preserving the existing public metadata contract without a second lookup justifies that bounded duplication. |
| 2026-09-23 | Independent agent review, round 1 | BLOCKED | Accepted all seven findings. The revision now makes system status and task list/stats client behavior mirror CLI selection through a shared resolved-context collector; distinguishes absent old-writer projections from present invalid projections; specifies an exact version-1 validator and degraded shape; limits state appends to current-view edges rather than every task-log report; supplies exact [CC-2.2], [CC-2.4], [CC-2.5], Quick Reference, and redaction-setting text; adds a bounded warning latch for state-write failure; declares the hardening triggers; and removes pipeline code from the expected writer scope. The user's later client-mirroring requirement also makes `all` canonical while retaining `include_terminal` as a one-release alias. |
| 2026-09-23 | Independent agent review, round 2 | BLOCKED | Accepted the `--all` and degraded-reconciliation findings: terminal log proof now wins deduplication, otherwise the selected current row (including degraded invalid state) wins; reconciliation is an exact three-key mapping with a bounded reason-code enum and deterministic first-failure order. Clarified the already-intended implementable alias signature and exact `CommandUsageError` cases. The status constant remains deliberately unchanged in `_constants.py`; the plan now names it as an inspected dependency and requires the existing constant-to-Literal enumerable-contract test to remain firing, rather than manufacturing a no-op production edit. |
| 2026-09-23 | Independent agent review, round 3 | PASS | Confirmed R2-F1 through R2-F4 are resolved: exact `--all` precedence, exact degraded reconciliation and deterministic reason codes, implementable client alias/error semantics, and unchanged shared lifecycle-status enumeration with its equality test. No remaining actionable ambiguity in the scoped findings. |
