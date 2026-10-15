# Task-Owned Observation and Read Separation

Status: completed
Source specs: docs/specifications/04-SimpleBroker_Integration.md [SB-0.4a]; docs/specifications/13C-Using_Weft_With_Django.md [DJ-8.3], [DJ-10.1], [DJ-11.1]; docs/specifications/00-Quick_Reference.md [QR-4]; docs/specifications/01-Core_Components.md [CC-2.2], [CC-2.4], [CC-2.5]; docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-5], [MF-6]; docs/specifications/07-System_Invariants.md [OBS.6], [OBS.6a], [OBS.13], [LIVENESS.R2], [LIVENESS.R3], [LIVENESS.R6]; docs/specifications/10-CLI_Interface.md [CLI-1.2], [CLI-1.3]; docs/specifications/14-Python_API_Surfaces.md [PY-2]
Superseded by: none

Class: 5. This changes the public observation contract, a task-state payload,
retention policy, and command/client read-path changes. Hardening applies.
Plan type: implementation with spec revision. Implementation and spec promotion
authorized by the owner on October 15, including the three implementation watchouts
recorded in the execution log below.
Promotion strategy: A, requirement text first; implementation mappings and code
backlinks land together in their owning implementation slice.

## 1. Goal and design decision

Make task-owned queues the complete ordinary observation surface. TaskMonitor
becomes a private telemetry consumer, never the database queried by status,
result, control, or client handles. Current observation and retired history have
separate contracts. This is a deliberate public change, not a compatibility
refactor disguised as simplification.

The separate [monitor plan](./2026-10-14-task-monitor-ingest-and-retirement-plan.md)
changes telemetry reclamation only after this read separation is verified. It
retains recovery bookkeeping that still owns evidence. Neither plan measures
success by lines removed or tables eliminated.

On October 14 the owner explicitly allowed public contract changes when they
bring Weft into line with its program theory. The chosen tradeoff is a bounded
observation window, with honest unavailability after route evidence expires.
Do not add another durable route index, consult external logs at runtime, or
silently guess standard routes to preserve unlimited bare-TID lookup.

### Design rationale and complexity budget

The implementation should look like ordinary, well-factored Python: one immutable
observation-config value, pure parsers/reducers, and the existing acquisition and
cleanup owners. Parse untrusted queue payloads once at their entry point; pass
validated values internally. Do not add a resolver registry, strategy hierarchy,
generic evidence framework, or wrappers that only forward arguments. Shared
validation earns its place because display, settlement, and deletion must agree;
an extra abstraction needs similarly concrete callers and hidden complexity.
Keep cohesive owners together rather than extracting by file size.

| Established principle | Application and consequence |
|---|---|
| Single-writer materialized view | A task publishes its own current projection. Bulk reads sample the last successful publication; they do not reconstruct a synchronized global state or repair it with hidden history reads. |
| Authority ordering, then source-local time | Local task completion outranks terminal projection, which outranks supervisor failure. A newer lower-authority timestamp cannot win. Encode this in the existing evidence selector, not a new merge engine. |
| Conservative reclamation | Eligibility and age are independent predicates. LivenessMonitor owns retirement; Monitor respects the row-presence hold and the latest qualifying retention anchor. Recheck at deletion; do not duplicate liveness probes or create a second lifecycle state machine. |
| Event-driven deadline scheduling | Existing due work defers a retirement-ready terminal row until expiry. New evidence invalidates that deferral. Reuse the reactor scheduler rather than adding polling, another timer service, or a retention cache. |
| Bounded cyclic scanning | One transient last-examined-TID cursor gives deferred and actionable cleanup families a fair turn within the existing slice budget. It contains no task truth and can be lost on restart. |
| Little's Law for retained inventory | At steady completion rate λ and mean retained lifetime R, retained terminal inventory is approximately λR. The configured minimum gives a baseline; holds and cleanup delay add lifetime. This predicts population and scan work, not latency. Measure the existing O(N) namespace scan before adding an index or cache. |

The two nested objects are separate because display evidence and route
configuration can remain usable independently. Their strict versioned wire
schemas protect operational decisions; they are not licenses to repeat
validation throughout the code. Use the repository's normal immutable value
style and existing serialization tools, not a new schema framework.
Subscribe-before-read remains the existing defense against missed wakeups.
No new transactional snapshot protocol or lossless historical observer is implied.

The irreducible costs are a bounded observation lifetime, possible bulk-status
lag after a failed publication, and a larger retained namespace. Prefer stating
those limits to adding recovery stores or hidden fallback reads. Keep explicit
raw history as a separate acquisition path, then use the existing shared reducer;
do not build separate status engines for each flag combination.

## 2. Baseline and required reading

Review baseline: Git `9b7ada69a21474346b44700d31157819819e62a6` plus the
October 14 working-tree versions identified below. Other work was modifying
family scheduling concurrently. These hashes identify the read baseline, not a
claim that HEAD contains these contents. Compare `git diff <baseline> -- <path>`
and current hashes before implementation; rebase overlapping decisions rather
than reverting another slice. Promotion baseline: October 15 working tree at
`c9fabb248752a220d7393d8afd30bd8418db9277`, with contract promotion and
implementation mappings recorded in §9. No separate promotion commit was made.

| File | SHA-256 at review baseline |
|---|---|
| `docs/specifications/05-Message_Flow_and_State.md` | `aa0612b5c66679a090ec744ee150a1e94a4395625fec28d6c4b93f4525ad7c7e` |
| `docs/specifications/07-System_Invariants.md` | `89efe72c64842f6bab4d00913e4cf494c8dd1fef9057d15fb6de9d0729d0587a` |
| `docs/specifications/10-CLI_Interface.md` | `5f955463149cfc5a2ce3918f9db78eefd473fe6c7381ee8ef68132e49395db1e` |
| `docs/specifications/14-Python_API_Surfaces.md` | `a93627fb5f4b8c909e1e9dd4935d99cefcc15b9ca0b99c6f30cb3cfc913fb467` |
| `weft/core/tasks/base.py` | `89a73cf78ba019e3ba417921385108564ed230a6d4422b0a818085510a115b01` |
| `weft/commands/system.py` | `bd230c3e1102141de8477af9cdb88551a8bd591c15932ca5ee47f5852c2a9fb1` |

Read `docs/program-theory.md` [WT-2], [WT-4], [ALT-WT-009], [ALT-WT-012],
`docs/agent-context/engineering-principles.md`, and the writing-plans,
hardening-plans, testing-patterns, adversarial-acceptance-probes, and
review-loops-and-agent-bootstrap runbooks. Read the source sections above before
code. `docs/lessons.md` Golden Rules and the May 31/June 10 checkpoint lessons
explain why an observation and a deletion proof are different things.

Related plans are context, not adopted specifications:

- [September current view](./2026-09-23-task-state-current-view-plan.md): inherit
  its compact projection, pure reducer, redaction, publication-edge design, and
  validation discipline. This revision replaces its compatibility log fallback,
  historical `--all` union, and retained Monitor lookup with the explicit
  contract below. Do not independently execute its conflicting tasks or client
  alias migration. Its review history remains historical evidence.
- [Result observation](./2026-10-10-result-observation-simplification-plan.md):
  preserve settlement, persistent boundaries, subscriptions, and accepted
  configuration; revise only evidence acquisition and route discoverability.
- [Supervisor projection](./2026-10-13-supervisor-terminal-projection.md): private
  Monitor reconciliation remains independent. Do not make LivenessMonitor a
  verdict publisher or confuse a supervisor failure with task-owned terminal proof.

### Actual owners

`BaseTask._build_tid_state_payload` and `_register_tid_state` append snapshots;
`_report_state_change` publishes completion before terminal mapping. Manager and
TaskMonitor extend the snapshot builder. Preserve their lineage, launch custody,
and passive diagnostic fields. `weft/core/task_state.py` owns suffix-bound
newest-valid selection. `weft/liveness/policy.py` and
`weft/core/tasks/liveness_monitor.py` own age/probe/exact-delete policy.

`weft/core/task_evidence.py` owns shared evidence and terminal selection.
`weft/commands/_task_snapshot_reducer.py` owns the pure public projection.
`weft/commands/system.py`, `tasks.py`, `_task_history.py`, `_result_wait.py`,
`events.py`, `result.py`, and `run.py::_stop_interactive_run_session` own
acquisition and settlement (including interactive shutdown). CLI adaptation is
in `weft/cli/app.py`, Python adaptation in `weft/client/_namespaces.py` and the
existing handle implementation. There is no `weft/core/liveness.py`,
`_update_tid_mapping`, or `weft/cli/status.py` to modify.

Comprehension checks: Why does a terminal-looking mapping not prove process
exit? Why can a custom outbox outlive its discoverability? Which result-policy
branch needs `stream_output` even after mode and routes are known?

## 3. Contracts and invariants

### 3.1 Two small task-owned values

Add `projection` and `observation` to each complete task-state snapshot. Keep
all existing liveness/runtime/lineage fields; neither object changes minimum
liveness validity. Use simple validated values and pure conversion functions,
not a pluggable resolver or a second snapshot class hierarchy. Put the shared
pure validators for both nested objects in `core/task_evidence.py`; command
reducers and LivenessMonitor use the same projection validator. Monitor cleanup
uses snapshot presence and shared local-completion validation; it does not need
a second projection-based retention decision.
`commands/_task_snapshot_reducer.py` keeps public view reduction, not a second
validation implementation. Core code never imports commands.

`projection` has exact keys: required `version` (exact int 1), `status` (member
of `TASK_LIFECYCLE_STATUS_VALUES`), nonblank `event`, nullable nonnegative exact-int
`started_at`, and redacted JSON-object `metadata`. Optional `completed_at`
(nonnegative exact int), `return_code` (exact int), and `error` (nonblank string)
are present only when non-null and terminal. Completion cannot precede start;
the outer snapshot `terminal` flag must agree with projected status.
Both lifecycle timestamps are Unix nanoseconds, as in `TaskSpec.state`. Reject booleans as integers.
Reject extra keys and unsupported versions. Observation time is the broker row
ID; duration is computed by the reader. No results, arguments, environment,
full TaskSpec, derived runtime description, or reconciliation are copied.

`observation` has exact keys: `version` (exact int 1), `mode`
(`one_shot|persistent|pipeline`), `stream_output` (bool), and `routes`. Routes
have exactly `inbox`, `outbox`, `ctrl_in`, `ctrl_out` (nonempty resolved queue
names) and `pipeline_status` (nonempty name for pipeline; null otherwise).
Mode is derived once from the resolved TaskSpec: pipeline role first, otherwise
persistent OR interactive, otherwise one-shot. Pipeline status honors the
existing runtime metadata override. These queue identifiers are operational
routing metadata, not secret payloads. Projection metadata uses existing
TaskSpec redaction; do not copy arbitrary event extras.

One immutable internal observation-config value in `core/task_evidence.py`
serves accepted TaskSpecs and parsed snapshots. Adapt `ResultMaterialization`
to retain this value instead of manufacturing a partial TaskSpec or retaining
independent `outbox_name`/`ctrl_out_name` copies. Read those routes from the
canonical value. Retain full accepted TaskSpecs where their existing callers
need them. Convert at the
boundary once; downstream mode/routes/stream checks use the canonical value.

The newest liveness-valid row is selected first. Invalid or missing nested
objects never authorize an older snapshot, a log scan, a guessed route, or
mapping deletion. Projection and observation validity are independent: valid
status can display while route metadata is unavailable, and valid routes can
collect output while current display is degraded. Invalid projection displays
`unknown`, safe identity/runtime fields, empty metadata, and a bounded
`task_state_projection_invalid` diagnostic; absent projection uses
`task_state_projection_unavailable`. No field contents enter diagnostics.

A validated terminal projection is task-authored **lifecycle** evidence, not
only display data. Extend the shared `TaskEvidenceSnapshot` selection boundary
in `core/task_evidence.py` to carry it with source `task_state`, classification
`terminal_projection`, its broker row ID, and no acknowledgement targets.
For conflicting lifecycle outcomes, a validated exact-TID task-authored local
completion wins over the terminal projection, and either wins over supervisor
terminal evidence. Do not let a later snapshot timestamp invert this order.
Existing outbox collection/settlement stays mode-aware; ordinary output and
persistent batch completion do not by themselves become task termination.
Nonterminal projections never overrule task-authored completion.

With resolved configuration and no task-authored local completion, a terminal
projection may settle the task through the existing output/grace rules, including
when only supervisor completion is present. It cannot fabricate stream-end
frames, persistent work-item IDs, batch boundaries, acknowledgements, or missing
output. A terminal projection without usable configuration still displays its
outcome in status, but result acquisition follows the metadata-unavailable rule
in §3.3: it cannot know which queues or collection mode to use. Include the
observed terminal status in reconciliation, without claiming a collected result.
Unresolved-route controls still fail before writing; when routes are resolved,
terminal evidence follows the existing already-terminal control behavior.
These rules do not change OS liveness: terminal publication precedes process
exit and cannot authorize a reaper to assume the process is gone.

### 3.2 Publication and retention

Keep one append-only publisher, eager initial visibility, terminal-success
latch, completion-before-terminal-mapping order, and edge-triggered updates.
Refresh projection on lifecycle, PAUSE/RESUME, activity, runtime, and existing
bounded diagnostic edges. A persistent `work_item_completed` edge may update
the display event but never makes the task terminal; terminal fields remain
absent until the task itself terminates. Poll reports and manager child reports
alone do not republish state. Preserve the last display event in owner-local state. Failed
publication stays nonfatal, warns once per consecutive failure run without
payloads, and retries on the next owner edge. Do not use `max_messages=1` or
read-before-write comparison.

Add `WEFT_TASK_OBSERVATION_RETENTION_SECONDS` in `weft/_constants.py`, default
172800 (48 hours), finite and at least `LIVENESS_MAPPING_MIN_AGE_SECONDS`
(currently 2400). It is a minimum for the newest liveness-valid snapshot **with
a validated terminal projection**, and for standard task-owned terminal
`ctrl_out`/outbox evidence. It is separate from raw-log retention. Nonterminal
snapshots, legacy snapshots without a valid terminal projection, and
superseded/malformed history keep their existing age/retirement rules. Invalid
nested data does not invalidate liveness evidence or permit earlier deletion
than those existing rules. A live or otherwise protected newest row never
becomes deletable merely because it is old.

Liveness evidence decides whether cleanup is allowed; retention evidence decides
when. Preserve the existing probe, unknown-timeout, generation, and custody
rules. Only after those rules permit retirement may a validated terminal row
wait solely for its observation floor. Reuse the existing reactor due scheduling
to defer that row to its retention deadline instead of probing every five
seconds for 48 hours. New row/generation evidence invalidates the deferral. At
the deadline, obtain current liveness authorization and recheck generation,
launch custody, and older-row absence before exact deletion. Restart may
reconstruct this transient deferral from queues; it creates no durable index.
Terminal lifecycle publication alone is never proof of OS process exit.

Newest terminal-snapshot age is measured from its broker row ID by
LivenessMonitor. Monitor uses snapshot presence as the queue-retention hold;
it does not also compute a projection-based age. While the row exists the hold
protects standard completion/output, including when observation metadata is
invalid; after LivenessMonitor retires it the snapshot's retention period has
already elapsed. This avoids duplicating one retention decision in two owners.

With no task-state hold, standard completion/output retention uses the latest
broker row ID among a validated exact-TID task-authored local terminal completion
and the existing Monitor terminal-evidence anchor when available. Retain the
existing Monitor anchor semantics; newly acquired local anchors must be actual
task termination, not PONG, supervisor failure, ordinary output, or persistent
work-item completion. Read the standard control source without consuming it;
this requires no route reconstruction. Only otherwise actionable families
without a snapshot hold need that read, even when there is no Monitor record.

Absence of a Monitor record is not absence of task-owned evidence. Use the
name-derived TID-age fallback only after the relevant queue reads succeed and
no qualifying terminal evidence exists; read errors defer cleanup. Feed the
same evidence/clock rule through candidate selection, deferred-work selection,
and the final destructive boundary. Immediately before deletion, revalidate
custody/liveness and reread task-owned terminal evidence so a candidate selected
before a fresh terminal publication cannot delete that publication. Preserve
existing exact-ID/covered-horizon safeguards. For standard ctrl_out/outbox only,
a current liveness-valid task-state row is an additional deletion hold, even
when terminal: wait for LivenessMonitor to retire it under its existing process
and custody rules. Recheck this row-presence hold at the destructive boundary;
read failure retains the queues. Do not use TaskMonitor's current
`_nonterminal_mapping_row_tids` filter for this hold: it deliberately omits
terminal rows. Do not widen `_active_runtime_tids` globally, since immediate
control/inbox and reserved policies are unchanged. TaskMonitor adds no runtime
probes and writes/deletes no task-state rows. Snapshot absence still requires
existing launch-custody and retention checks; it is not a new proof of death.
This uses the existing sole-deleter handoff instead of asking TaskMonitor to
reprove OS liveness. Do not add a parallel cleanup framework, Monitor lookup
requirement, or another durable store.

In record-backed lanes, a snapshot hold or an unexpired retention anchor narrows
the deletion plan: remove ctrl_out/outbox from the eligible queue set, perform
any still-eligible ctrl_in/inbox cleanup, and record the existing
`mark_task_controls_deleted` progress. A hold is not a custody failure and must
not leave the whole record pending at the front of the limited SQL query. Only
launch custody, read/deletion errors, and actual incomplete eligible work retain
their existing pending semantics. Preserve the existing later path for retained
queues: open-control reconciliation where applicable, and recordless cleanup
after normal Monitor-record retirement. No new cursor or SQL lifecycle is added
to retention-only record-backed work. Whole-family nonterminal protection is a
separate case: exclude it before reporting or effects, without marking cleanup
complete. If protected records fill the limited query prefix, advance a transient
`(last_message_id, tid)` keyset waypoint through the existing worker handoff and
store query. A completed pass wraps to the start at normal cadence; candidate
and final guards still decide protection. This is bounded pagination, not a new
persistent lifecycle, schema, or index. Retention is a minimum, so a longer-lived Monitor record
may postpone final cleanup. Verify that held records do not block a later ready
record and that residual queues remain eligible through the existing handoff.

Normal terminal publication therefore retains routing for at least the standard
result window. Missing final snapshot publication, abrupt death, administrative
pruning, and dump/load (which excludes runtime state) cannot promise a fresh
routing window. A retained valid local completion still protects standard
completion/output by its own clock, and can supply configuration under §3.3.
Never synthesize a terminal outcome or renew a row on behalf of a dead task.
Custom/shared queues remain outside automatic cleanup and may outlive route
discoverability. Reserved-policy and reserved-cleanup age protections are unchanged.

The retention wiring is part of this read contract, even though some owners
live under Monitor. Add the validated observation-retention value to
`TaskMonitorRuntimeConfig.from_config` and the LivenessMonitor configuration
path, loaded through `_constants.py`. Keep distinct parameters rather than
reusing `mapping_min_age_seconds` for two policies:

| Owner | Required clock/action |
|---|---|
| `LivenessMonitor._read_mapping_history` / `reduce_mapping_history` | Existing history minimum, including malformed rows; unchanged. |
| `LivenessMonitor._apply_probe_result`, due scheduling, `_retire_mapping_row` | Existing retirement authority; terminal-projection-only observation floor and retention-only deferral, with fresh authorization before exact deletion. Nonterminal retirement stays unchanged. |
| `runtime_control.terminal_task_runtime_queue_cleanup_plan` and `stale_service_owner_runtime_queue_cleanup_plan` | Observation clock for standard completion/outbox; existing immediate-control rules unchanged. |
| `dead_task.dead_task_queue_cleanup_plan` | Use snapshot hold plus local-completion retention evidence; separate observation clock for ctrl_out/outbox from reserved clock. TID-age fallback only after successful evidence reads find no qualifying anchor. |
| `runtime_control.runtime_dead_task_record_probe_tids`, `select_runtime_dead_task_cleanup_candidates`, `_has_retention_deferred_only_work` | Carry both clocks and terminal retention evidence through candidate/probe/deferred selection by queue role; selection cannot bypass or indefinitely defer the actual cleanup rule. |
| `TaskMonitor._run_dead_task_cleanup_slice`, `_delete_dead_task_control_queues`, `_delete_covered_control_rows` | Acquire task-owned evidence even without a Monitor record; revalidate the same retention and writer/custody guards at deletion. |
| `Manager._observe_admission_usage`, `_admission_mapping_is_live`; `endpoints._record_owner_is_live` | Audit consumers of retained mappings. Preserve live-process accounting and existing nonterminal/unknown behavior; retained dead terminal observations must not become resource usage or live endpoint owners. |
| Monitor store summary/parent age, raw external output, reserved workers | Retain current clocks. Keep reserved default derived from configured log retention and its explicit override. |

Update `MaintenanceWorker` call sites and cached configuration diagnostics in
`task_monitor.py`. Do not globally replace the old retention key. Test unequal
clocks in both directions, absent Monitor records, and invalid/nonfinite config.
The observation floor does not create terminal proof where none exists.

Keep queue acquisition outside pure selection policies. Use already-acquired
snapshot evidence for cheap classification. New control-anchor reads occur only
for otherwise actionable families without a task-state hold, within the existing
cleanup-family limit and slice deadline, not in an unbounded `_has_retention_deferred_only_work` loop.
The recordless lane reads them only after excluding families with Monitor
records; record-backed lanes apply the same final retention rule in their own
bounded work. Charge every inspected family, including a retained/read-failed
one, to the work budget and report retained ones as `deferred_retention`.
Bounded deferral must not repeatedly select the same old-TID prefix: carry one
transient last-examined-TID cursor through the existing worker progress handoff,
advance over deferred families, and wrap after the inventory end. Restart may
start that sweep again. This is a scan cursor, not a route/retention cache or
durable index. Test a deferred prefix larger than the family limit followed by
an eligible family; bounded slices must eventually reach the eligible family.
The acquisition loop owns reads and budget accounting; pure policy functions
receive the acquired evidence rather than performing I/O through new callbacks.

In the recordless lane, a slice budget and pass completion are different facts.
Deferred, held, and read-failed families consume the read budget but do not by themselves set
`pending`, `family_limit_hit`, or `PolicyProgress.waypoint_reached`.
Derive those traversal signals from one fact: the inventory pass is incomplete
(not merely that a slice reached its read budget). An incomplete pass may request
the next slice; once the cursor reaches its end, a deferred-only pass is complete
and returns to the normal discovery interval. Preserve actionable retry/error
reporting without converting passive retention into perpetual catch-up. The
record-backed lanes use the partial-cleanup progress rule above. Verify that more than one slice
of deferred families, with no eligible work, finishes a pass and restores normal
cadence. The cursor is a traversal waypoint, not evidence that cleanup is owed.

Retention increases the inventory scanned by the existing five-second namespace
refresh even when terminal probe work is deferred. Measure representative
completion bursts on both backends: record retained row count, namespace reads,
probe submissions, cleanup-slice anchor reads, reactor progress, and admission
latency before/after. The 72-fold default retention increase is a population multiplier at steady task
arrival rate, not a measured slowdown. Accept only if retained retirement-ready
rows avoid recurring probes, ordinary unknown rows keep their current retirement
bound, and foreground work makes progress at the original test pressure. Record
remaining scan cost explicitly; if it impairs progress, stop and resolve that
measured issue before rollout rather than inventing an index preemptively.

### 3.3 Reader matrix and explicit capability changes

| Surface | New contract |
|---|---|
| `status`, `task list`, stats, default Python status/list | Current nonterminal projections; one namespace scan and shared broker scope; no task-log, Monitor-store, per-task control/outbox, or active PING reads. |
| `--all` / `include_terminal=True` | Include retained terminal projections from the same inventory; not a union with retired history. |
| `--log` / `include_log_history=True` | Explicit retained raw-log reconstruction unioned with current projections; terminal task proof wins, otherwise valid current projection wins. No Monitor tables or external-log reads. An empty retained log is an honest empty historical contribution. |
| Known-TID status/result/control | Accepted configuration or validated task-state observation metadata; configured local queues and current runtime evidence. No implicit global-history or Monitor-store fallback. |
| Task event/watch APIs | Raw lifecycle following remains explicit and best effort while rows are retained. Local completion still ends result-following; do not invent missing intermediate events from snapshots or promise lossless observation against a destructive monitor. |

Bulk inventory reports the latest successfully published projection, not a
linearizable completion view. If local completion succeeds but terminal snapshot
publication fails, bulk status may remain nonterminal until another successful
owner publication or normal retirement. Known-TID observers that read local
completion still report terminal. The precedence rules apply to evidence the
surface actually acquires; bulk reads do not add per-task queue peeks to hide
this best-effort publication gap. Explicit history can resolve it only while the
relevant raw terminal event is retained. Document and test this distinction.

CLI adapters pass inclusion before acquisition, rather than filtering a full
history afterward. Preserve existing task-list Python `status` and
`include_terminal` keywords. Add `include_terminal=False`, `status=None`, and
`include_log_history=False` to `SystemNamespace.status`; add
`include_log_history=False` to list/stats and the shared command entry points.
`--log` implies historical inclusion; contradictory status filters simply
filter the resulting union. Reject `--log --watch` as a typed usage error.
No `all`/`include_terminal` alias migration is included.

Internal service snapshots use already-acquired service registry, projected
child identity (existing role/internal service metadata), internal spawn, and
Manager internal-reserved evidence. Remove their independent log replay on the
ordinary path. Do not issue a new Manager PING. In the launch-before-child-state
interval, available reserved evidence is shown; absent evidence is `unknown`,
not death. This loses the transient log-only launched/PID detail deliberately.
On explicit `--log`, the existing Manager launch-event enrichment is allowed.

For unresolved bare-TID waits, register task-state interest and a read-only standard
`T{tid}.ctrl_out` discovery probe before reading. A standard control envelope
may supply configuration only if task-authored, exact-TID bound both outside
and inside its TaskSpec, and every immutable field needed for mode, stream
framing, and resolved routes is explicitly present and validates. Convert only those required fields to the
same canonical observation-config value used by accepted TaskSpecs; do not
require reconstruction/validation of an unrelated full executable TaskSpec.
Require `spec`, `io`, and `metadata` to be JSON objects; require explicit actual
booleans for `spec.persistent`, `spec.interactive`, and `spec.stream_output`.
A missing `metadata.role` means non-pipeline only within a validated metadata
object; a redacted/malformed metadata object or role makes mode unavailable.
For a pipeline, require its explicitly resolved status route; an absent or
redacted runtime/status-route container cannot fall back to `P{tid}.status`.
Do not reuse permissive `_result_mode`, `is_pipeline_taskspec_payload`, or
`pipeline_status_queue_name` as discovery validators. Accepted full TaskSpecs
may still use their normal default resolution before conversion.
Redaction of arguments, environment, or unrelated metadata does not invalidate
otherwise complete configuration. Missing, malformed, or redacted required
fields (including the pipeline role/status-route override when applicable)
make configuration unavailable. Reject the existing redaction sentinel
`[REDACTED]` in required fields even where its string type would otherwise pass;
that sentinel is not an inferable route. Do not silently substitute a default
route, mode, or stream flag for missing/redacted fields. Never claim output from
guessed queues. Once routes resolve, subscribe to
them before reading and retain configuration even if state later disappears.
No discovery-triggered control write or output claim occurs before resolution.

With no timeout, unresolved result acquisition returns `TaskResult(status="unknown")`
with reconciliation classification `observation_metadata_unavailable`, an
explanation, and CLI exit 1. A finite timeout permits discovery on the registered
queue interests until its original deadline, then the existing typed observation
timeout applies (exit 124); no synthetic task timeout is published. Accepted
handles retain their configuration and can wait before child initialization.
Snapshot status returns an unknown diagnostic for a valid full TID lacking
usable observation evidence; it does not assert nonexistent or failed. Bare-TID
control writes with unresolved routes fail explicitly at the typed command
boundary, exit 1. Special existing non-task selectors keep their own contracts.

After metadata expiry, a retained exact task-authored standard completion may
still establish an outcome/configuration; otherwise bare-TID lookup is
unavailable even if custom output exists. Already-bound handles still work.
No automatic archive reader is added. An operator can inspect external JSONL
with ordinary tools. This observable loss of indefinite historical lookup,
including after dump/load, must appear in README/CHANGELOG, not only this plan.

Preserve task-owned-before-supervisor terminal precedence, `wrapper_lost`
provenance, persistent batch boundaries, stream framing, output grace, exact
claims, shared-control TID filtering, and cancellation. A stale nonterminal
projection cannot outrank local terminal proof on surfaces that acquire that
proof; bulk inventory has the explicit publication-gap limit above. Snapshot
projections do not invent work-item completion records.

Interactive shutdown in `commands/run.py::_stop_interactive_run_session` is an
ordinary observer too. Bind its accepted configuration (or resolve through the
same typed discovery boundary before any control write), watch the resolved
local queues, and remove its direct global-log/Monitor acquisition. Preserve
input closure, keyed STOP/ACK matching, cancellation isolation, and the existing
STOP deadline and KILL escalation behavior. Remove its obsolete helper imports
only after migrating this caller; no dangling imports or private history fallback
may survive the acquisition cleanup.

## 4. Proposed Spec Delta

These paragraphs defined the spec-promotion slice, performed before code under
the October 15 implementation authorization. They replace conflicting sentences
and carry this plan's backlinks; §9 records implementation evidence.

### `07-System_Invariants.md` [OBS.6], [OBS.6a] — append

> Task snapshots carry independently validated `projection` and `observation`
> objects. Projection version 1 carries current lifecycle status, display event,
> lifecycle timestamps in nanoseconds, terminal outcome fields, and redacted public metadata.
> Observation version 1 carries immutable collection mode, stream framing, and
> resolved inbox, outbox, control, and optional pipeline-status routes. Their
> exact schemas and validation rules are defined in [MF-5]. Invalid nested data
> does not invalidate otherwise valid liveness evidence. Readers never revive
> older nested data behind the newest liveness-valid row. Publication remains
> task-owned, append-only, edge-triggered, and best effort. Terminal mapping
> publication follows local completion publication. No other component writes
> task verdicts into the task's snapshot queue.

### `05-Message_Flow_and_State.md` [MF-5] — replace observation-source and configuration-discovery rules

> Ordinary status, result, control, and client observation use task-owned queues,
> never Monitor tables or external log files. Bulk inventory reads newest-valid
> task snapshots. Known configuration remains bound to the observation; bare-TID
> acquisition reads the snapshot's observation object or a validated exact-TID,
> task-authored standard completion containing complete immutable observation
> configuration. Unrelated redacted fields do not disqualify it; missing,
> invalid, or redacted required collection/routing fields do. No required
> field is defaulted during discovery.
> A standard completion queue is only a read-only discovery candidate, never
> permission to assume default transport. Register queue interests before reads
> and register discovered routes before consuming output. Pure point queries
> use a broker read without an activity subscription. Missing configuration
> is unavailable evidence, not task failure or proof of absence. Explicit raw
> lifecycle/history operations may read retained `weft.log.tasks`; they do not
> provide a fallback for ordinary readers. Monitor collations are private export
> and cleanup state, never public lifecycle or routing authority.

Promote the exact schema/type/validation paragraphs of §3.1 above immediately
after this paragraph as the version-1 [MF-5] schema definition (verbatim, excluding
the implementation paragraphs naming `ResultMaterialization` and the internal
`TaskEvidenceSnapshot` representation). Promote §3.1's lifecycle-authority and
precedence rules too, retaining their behavior but omitting internal class names. Promote §3.2's
retention paragraphs verbatim into the [MF-5] cleanup boundary, replacing the
standard ctrl_out/outbox coupling to task-log retention. [LIVENESS.R3] adds:

> Only newest liveness-valid rows with validated terminal projections additionally
> satisfy the configured task-observation retention minimum. Nonterminal rows,
> rows without valid terminal projections, and superseded/malformed history keep
> their existing retirement rules. After existing liveness policy permits
> retirement, terminal rows may defer to their retention deadline, with current
> liveness authorization and generation/custody revalidation before deletion.
> Probe authority, unknown-timeout policy, launch-custody protection, and exact-ID
> deletion ownership do not change.

### `10-CLI_Interface.md` [CLI-1.2], [CLI-1.3] and `14-Python_API_Surfaces.md` [PY-2]

> Status/list default to current nonterminal task snapshots. `--all` includes
> retained terminal snapshots. Bulk inventory reflects the latest successful
> snapshot publication and can lag local completion when final snapshot
> publication fails. Known-TID observation of local completion still takes
> precedence over stale projections. `--log` explicitly adds retained raw history and
> implies historical inclusion. `--log --watch` is a usage error. Python adapters
> expose `include_terminal`, `status`, and `include_log_history` with the same
> selection semantics. Ordinary reads do not query Monitor storage. A result
> without a timeout and without resolvable observation metadata returns unknown
> with classification `observation_metadata_unavailable` (CLI exit 1). A finite
> timeout waits on discovery interests until the caller's deadline and then
> uses the existing observation-timeout error (exit 124). Snapshot absence is
> unknown evidence; unresolved task-control routes produce a typed error before
> any write (exit 1). Retained task-owned terminal proof may still establish a
> result. Expired metadata does not authorize inferred routes. Event streams
> expose retained/live raw events and do not guarantee lossless history.

`00-Quick_Reference.md` [QR-4] adds this exact queue description:
> `weft.state.tasks.<tid>` is append-only task-owned current/retained observation
> and runtime evidence, selected newest-valid and deleted only by LivenessMonitor.

`01-Core_Components.md` [CC-2.2], [CC-2.4], [CC-2.5] add:
> BaseTask owns the projection and immutable observation descriptor on its
> existing snapshot publication path. Runtime/task subclasses extend that path
> without replacing shared fields or introducing a second writer.

Configuration text in the owning CLI/config sections adds:
> `WEFT_TASK_OBSERVATION_RETENTION_SECONDS` defaults to 172800 seconds, accepts
> finite values at least 2400, and governs newest-valid terminal-projection snapshots and
> standard terminal result/completion retention. It does not change raw-log or
> reserved-message retention and is not an exact expiry or an audit guarantee.

At promotion remove the contrary terminal Monitor fallback text in [MF-5] and
[OBS.13], including implementation mappings; preserve its private monitor uses.
Reconcile [WT-2]/[WT-4] theory terminology, but do not invent nonexistent theory
section titles. The theory's queue-only direction is preserved, not relaxed.

## 5. Implementation slices (authorized October 15)

1. **Spec promotion and contract tests.** Files: the exact spec sections above,
   README/CHANGELOG, `_constants.py` only in its later implementation slice.
   Record promotion baseline and bidirectional links. Write red broker-backed
   tests proving ordinary reads work with empty raw logs and absent Monitor
   tables, and unresolved control never writes. Stop if the implementation
   still requires a historical fallback or an extra state owner.
2. **Task publication and retention.** Files: `_constants.py`,
   `core/task_evidence.py`, `core/tasks/base.py`, `core/tasks/liveness_monitor.py`,
   `liveness/policy.py`, `core/monitor/runtime.py`, and existing monitor cleanup
   policy owners listed in §3.2 (`policies/runtime_control.py`,
   `policies/dead_task.py`, `task_monitor.py`). Include the existing Monitor
   worker input/result handoff for the transient scan cursor.
   Reuse existing redaction, exact-ID reaper, task-state reader, and terminal
   latch. Update Manager/TaskMonitor overrides only to preserve extensions.
   Verify normal completion, failed publication, newest-invalid nested data,
   young/old history, late newer row, live old row, and launch custody. Verify
   terminal-only deferral and unchanged nonterminal retirement; audit Manager
   admission and endpoint ownership consumers. Wire task-owned retention evidence
   through selection and deletion, including recordless families and late evidence.
3. **Current inventory and service view.** Files: `commands/system.py`,
   `commands/_task_snapshot_reducer.py`, `commands/tasks.py`, `cli/app.py`,
   `client/_namespaces.py`. One collector; separate explicit history acquisition
   before reduction. Verify no ordinary log/Monitor opens, filtering/stats,
   client parity, redaction, retained terminal rows, service launch gaps, and
   bulk/known-TID divergence when final snapshot publication fails.
4. **Known-TID and control acquisition.** Files: `core/task_evidence.py`,
   `commands/_task_history.py`, `_result_wait.py`, `tasks.py`, `events.py`,
   `result.py`, `run.py`, existing client handle adapters and command error/rendering
   owners. Replace all implicit history/Monitor acquisition, including
   `known_tid_evidence`, `_await_control_surface`, `task_ping`, and ctrl route
   helpers, and `run.py::_stop_interactive_run_session`. Keep the existing
   settlement algorithm and interactive STOP/ACK/deadline behavior. Remove obsolete helpers
   after caller sweeps, not by a stale deletion list. Test every row of §3.3.
5. **Reconciliation and independent review.** Verify full contracts on both
   backends, mappings/backlinks, README/CHANGELOG differences, config no-op
   prevention, and ordinary-observation architecture boundary. Stop if tests
   require a hidden archive reader or weaken task-owned terminal precedence.

No execution, producer lifecycle payload, task identity, TaskSpec immutability,
reserved policy, or spawn mechanism changes are included. Public result
shape stays unchanged; the unavailable classification and exit behavior change
exactly as stated in §3.3. No new dependency or durable route queue/index is permitted.

## 6. Verification and acceptance

Use the smallest real interface that proves each contract. Pure parsers and
reducers take in-memory values and need no broker harness. Acquisition,
publication, subscription, and deletion claims use `WeftTestHarness` and real
queues/store transactions as relevant. Inject faults at actual broker/process
edges, never replace the decision under test. Extend existing tests rather than
creating a parallel suite for the new representation.

Relevant existing homes are `tests/tasks/test_task_observability.py`,
`tests/tasks/test_liveness_monitor.py`, `tests/core/test_task_state.py`,
`tests/commands/test_task_evidence.py`, `tests/commands/test_status.py`,
`tests/commands/test_task_commands.py`, `tests/commands/test_result.py`,
`tests/commands/test_result_settlement_policy.py`,
`tests/commands/test_result_activity_waits.py`,
`tests/commands/test_active_interactive_lifecycle.py`,
`tests/commands/test_control_observation_connections.py`,
`tests/core/test_client.py`, `tests/cli/test_status.py`,
`tests/tasks/test_task_monitor.py`, and
`tests/core/monitor/policies/test_runtime_control.py`. These are candidate homes,
not a requirement to edit every file or replay the same case through every layer.

| Contract | Smallest useful proof |
|---|---|
| Wire values and canonical configuration | Independent valid examples cover all fields and modes. Table-driven invalid equivalence classes cover unsupported version, type confusion, inconsistent terminal state/time, missing required routes, TID mismatch, and redaction at parent/required versus unrelated fields. Assert the resulting usable/unavailable classification. |
| Source selection and settlement | Existing owner tests cover local-task/snapshot/supervisor precedence, terminal projection without completion or routes, persistent batch boundaries, and streaming grace. Add cases only for the new source or changed acquisition; do not duplicate the existing settlement matrix. |
| Public acquisition and adapters | Real queues cover custom/shared routes, pipeline status override, accepted-handle prelaunch, unresolved finite/no-timeout behavior, and loss of metadata while bound. CLI/client tests cover their distinct argument/result/exit translation, including 1, 124, new flags, and empty-log `--all`/`--log`. |
| Retention and liveness | The concrete regressions below prove independent clocks, recordless preservation, destructive revalidation, fair bounded work, and unchanged unknown-row retirement/admission. |
| Ownership separation | Ordinary observation succeeds with absent Monitor tables and empty raw logs. One focused dependency/access guard may enforce the private-store boundary; no copied private-symbol inventory or source-text grep suite. Administrative `commands/task_monitor.py` remains allowed. |

This is a coverage checklist, not a cross-product of every mode, malformed field,
backend, CLI flag, and public entry point. Share cases when the same owner makes
the decision; add another layer only for a distinct wiring or broker-semantic
risk. Shared broker contracts run on both brokers; pure value tests do not need
backend parameterization. Combine restart and dump/load coverage with existing
lifecycle tests when they expose the same metadata-absence contract.

Negative assertions are limited to real forbidden effects: claiming output or
writing control without resolved configuration, prematurely deleting retained
evidence, violating private-store ownership, or issuing recurring probes for
retention-only work. Exercise the decision with an opportunity for that effect;
assert public outcomes and preserved queue contents first. Do not assert private
helper call order, exact SQL/peek counts, diagnostics prose, dataclass layout,
or the absence of arbitrary internal calls. Work-count tests assert the slice
budget or scaling property, not today's exact instruction sequence. Every new
regression must identify a credible broken behavior and fail for that reason;
existing stronger coverage is a reason not to add another test. No test-only
production exports, flags, or alternate lifecycle paths are permitted.

The following regressions must fire against the old or deliberately broken path:

- Complete a task created three days ago with no Monitor record. Snapshot
  presence holds standard completion/output; after snapshot loss, fresh exact
  task-authored local completion protects them by its own age. Cover successful
  absence reads using TID fallback, read failures deferring cleanup, and reserved
  policy under unequal clocks. A deferred prefix larger than the recordless
  cleanup budget must not starve a later eligible family or cause permanent
  catch-up. In record-backed cleanup, retained queues must not prevent recording
  eligible cleanup progress or reaching a later ready family.
- Select a recordless cleanup candidate, then publish terminal evidence before
  deletion. Revalidation preserves it. A still-live terminal publisher is not
  treated as proof of death. Expired evidence permits cleanup only after the
  existing liveness/custody guards hold.
- Preserve a dead terminal snapshot through the new floor without repeated probe
  submissions; a changed generation resumes observation. At expiry revalidate
  before deletion. Invalid observation metadata does not defeat a valid terminal
  projection's floor. Unknown/nonterminal rows retain the existing unknown-timeout
  and minimum-age behavior, including SQLite admission and endpoint ownership.
- Succeed at local completion publication and fail the following terminal
  snapshot append at the broker boundary. Known-TID status/result is terminal;
  ordinary bulk status shows its last successful projection without local peeks.
- Exercise the changed interactive acquisition with empty raw logs, absent
  Monitor tables, custom control routes, and unresolved-route write rejection.
  Reuse existing own-ACK, STOP deadline, and output-cancellation tests unchanged
  to preserve those contracts; do not label them new regressions against the old
  acquisition path or duplicate them.

Use broker row IDs with controlled ages and the existing clock seams for expiry
checks; do not wait 48 hours, lower the production minimum, or sleep to establish
ordering. For burst/load checks use the original workload and standard concurrency;
record the measured retention population and scan cost as required in §3.2.

Per-slice commands (standard concurrency, no acceptance `-n 0`):

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/tasks/test_task_observability.py tests/tasks/test_liveness_monitor.py tests/core/test_task_state.py -q
./.venv/bin/python -m pytest tests/commands/ tests/cli/test_status.py tests/core/test_client.py -q
```

Final implementation gates:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests
./.venv/bin/python -m pytest -m "" tests
./.venv/bin/python bin/pytest-pg --all tests
./.venv/bin/python -m pytest integrations/weft_django/tests
./.venv/bin/python -m pytest extensions/weft_docker/tests
./.venv/bin/python -m pytest extensions/weft_macos_sandbox/tests
./.venv/bin/python -m pytest extensions/weft_microsandbox/tests
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
git diff --check
```

These explicit roots are required: `pyproject.toml` defaults to `tests` only.
Put new shared observation/retention contract cases under `tests/` so the SQLite
and PostgreSQL gates both execute them. The PostgreSQL wrapper uses the supported
non-`sqlite_only` core suite and its standard worker policy. Consumer roots run
separately on their supported platforms with the required extras installed;
this plan does not require expanding their PostgreSQL support. Direct pytest
uses the SQLite test backend, not an inherited PostgreSQL selector. Record
platform/runtime skips and unavailable prerequisites; skipped required contract
cases are not completed gates.

Current CI consumer jobs use `-n 0`; that is a pre-existing matrix difference,
not authorization to lower concurrency after a failure. The direct commands
above keep repository-default concurrency, as required by [ALT-WT-017]. Existing
CI consumer jobs may supply supplementary regression evidence but do not replace
full-concurrency acceptance or close a failure at its original pressure. If a
consumer run exposes a load or oracle problem, follow [TS-0]'s stop/diagnosis
rule rather than quietly substituting a serialized pass.

Run this traceability command before promotion and after reconciliation:

```bash
../backstitch/.venv/bin/backstitch check \
  --repo-root /Users/van/Developer/weft \
  --no-config \
  --spec-root docs/specifications \
  --plan-root docs/plans \
  --code-root weft --code-root tests --code-root bin \
  --code-root integrations --code-root extensions \
  --format json \
  --output /tmp/weft-task-observation-backstitch.json
```

 Compare diagnostics by severity/code/path/message;
resolve all newly introduced reciprocal-link debt. A full-suite green run is not
claimed by plan metadata checks. Runtime acceptance: retain custom-route results
through the configured observation window on SQLite and PostgreSQL, then prove
explicit unavailability after allowed retirement without claiming data loss.

## 7. Rollout and rollback

Ship task writers and readers as one coordinated release; stop/drain old task
processes under the existing operational procedure before enabling the new
contract. No runtime legacy decoder or log fallback is added. Old retained rows
remain valid liveness evidence and display degraded observation until an owner
publishes again or normal retirement occurs. Existing historical data may become
unavailable to bare-TID commands; notify operators in the release notes.
Do not wipe state queues or restart running user jobs automatically.

This plan alone does not accelerate telemetry deletion. Before the monitor plan
lands, reverting readers/writers can use still-retained logs/store subject to
normal expiry; retention/public-contract reversal still needs a coordinated
release. After monitor drain changes, code rollback cannot restore deleted
history. Recover through preserved exporter state or explicit backup/restore,
never promise `--log` as a rollback mechanism. Plans land with explicit file-list
staging. No commit has been requested or made for this implementation.

## 8. Review, revisions, and deviation log

Original draft SHA-256: `ee13071bdd422ee0a51d7db5a611fd998e0b6e731d755fff39dc48ee3feea522`.
Its three author fresh-eyes checks claimed complete reader coverage and bounded
queue growth. The October 14 independent assessment supersedes those claims;
it found stale liveness owners, crash gaps, export loss, incomplete observation
metadata, incorrect defaults, and unsupported boundedness.

| Finding / decision | Disposition in this revision |
|---|---|
| R1 liveness contract | Preserve append-only ownership and probes; change only explicit newest-row retention, never synthesize verdicts. |
| R2 crash gap / R3 deferred loss | Separate monitor plan retains exact-delete and deferred evidence. |
| R4 routing lifetime | Full descriptor, independent observation window, explicit unavailable beyond retained evidence; no route guessing. |
| R5 incomplete summaries / wrong defaults | Monitor plan keeps full-pass summary eligibility and uses actual defaults. |
| R6 boundedness | Conditional progress claims; no hard storage guarantee. |
| R7 baseline/spec/gates | Concrete baseline, exact delta, promotion before code, actual files, complete gates. |
| Owner permits public changes for theory alignment | Remove historical Monitor fallback deliberately; record visible losses and rollout contract. |

Review protocol: independent reviewer reads both plans, proposed deltas, current
owners, and program theory. Return PASS/BLOCKED on implementability and robustness;
price added mechanisms against the responsibility they serve. Prefer a different
agent family; record limitations. Review before implementation, after each slice,
and at final reconciliation. Author must disposition every finding and obtain
verification of accepted fixes. Independent review availability: Claude could not authenticate (expired OAuth);
Gemini refused the untrusted workspace. Neither environment was changed.
A fresh same-family reviewer reviewed both drafts; this limitation is
explicit. Initial independent verdict: BLOCKED on F1 and companion F2.
Accepted-fix verification: PASS (October 14), scoped to F1/F2/C1 and any
new defects introduced by their fixes. No new defects were found.

Author fresh-eyes pass (October 14): found that a generic retention call-site
sweep left role-specific cleanup ambiguous. §3.2 now names every current policy
owner and keeps reserved/history clocks separate. Also clarified nanosecond
units, outer terminal-flag consistency, and the changed unavailable result
classification. Monitor review found no reason to replace its existing
checkpoint protocol; the companion retains it and its exact retry ledger.
Residual risks are bounded discoverability and loss of the redundant raw
telemetry copy, explicitly described above. Independent plan verification is recorded below;
implementation still requires separate authorization and runtime acceptance.

Deviation log: no implementation has occurred. The scope revision above replaces
the original plan rather than claiming compliance with its invalid premises.


### Independent review dispositions (October 14)

| ID | Finding | Disposition |
|---|---|---|
| F1 (P2) | Terminal projection authority/precedence unspecified. | Accepted. §3.1 now defines lifecycle proof, local task-completion precedence, metadata-unavailable result behavior, unchanged batch/stream boundaries, and separate OS liveness. §6 requires absent/conflicting completion tests. |
| F2 (P2, companion) | JSONL launch-row deletion silently removes special per-launch export detail. | Accepted in the monitor plan: explicitly retire the special report/keep-newest policy, retain existing deferred reports, and promote the [MF-5]/[MF-6] delta. |
| C1 | New retention value could accidentally change reserved cleanup. | Accepted in author self-review. §3.2 identifies policy owners, separate clocks, and unequal-clock tests. |


Review evidence: the independent reviewer inspected the plans against current
code/specs, then verified the accepted fixes in a separate pass. Read-plan
SHA-256 for that design review: `095fd14d4d25217774760b224086bd9617b51db8fa3d419c2a8a6c7148680fb5`.
Companion review SHA-256: `f79178452f9d9873863633873b8c3087efafb7457ab7161ce3df91402ce20553`.
The subsequent edits only record this verdict. Plan metadata tests passed
(4 tests), relative document links resolved, and `git diff --check` passed.
No runtime acceptance or spec promotion is claimed. No implementation deviations
have occurred because implementation was outside this request.


### October 15 code/theory review and accepted revision

Revision baseline: `53b4ea129db95d576c9f84832edd83f252780c06`, with concurrent
working-tree documentation changes left intact. This is a class-5 plan revision,
not implementation or spec promotion. The existing draft index row remains valid.
Earlier October 14 PASS results cover only their stated review scope.

| ID | Finding | Accepted action in this revision |
|---|---|---|
| O1 (P1) | Applying 48-hour retention to every newest row changes uncertain liveness/admission and extends repeated probe work. | §3.2 limits the new floor to validated terminal projections, keeps nonterminal retirement unchanged, defers retention-only probes, audits admission/endpoints, and requires measured inventory/load evidence. |
| O2 (P1) | Recordless cleanup can age fresh completion/output from a three-day-old TID. | §3.2 uses task-owned terminal anchors even without a Monitor record, carries the decision through candidate and final deletion, and preserves writer/custody guards. §6 requires late-publication races and independent anchors. |
| O3 (P2) | Interactive shutdown still imports and reads historical observation helpers. | §3.3 and slice 4 explicitly migrate `run.py::_stop_interactive_run_session` and verify its accepted routes, ACK, cancellation, and escalation. |
| O4 (P2) | Snapshot-only bulk reads cannot guarantee precedence for unseen local completion. | §3.3 and the exact spec delta state the best-effort bulk publication gap; known-TID precedence remains intact. |
| O5 (P2) | Whole-TaskSpec redaction rule rejects usable configuration and can accept sentinel routes. | §3.3 validates only complete required observation fields, rejects redacted required fields, and allows unrelated redaction without executable TaskSpec reconstruction. |
| O6 (verification) | Default pytest roots omit integrations/extensions and do not select PostgreSQL. | §6 names all consumer roots separately, the supported PostgreSQL core gate, skip handling, and original-concurrency load evidence; shared new contract cases run on both brokers. |

Author fresh-eyes pass: updated the operative contracts, exact promotion text,
owners, implementation slices, and firing cases together. Kept separate retention
clocks, the canonical config value, and explicit `--log`: removing these would
undo intended boundaries, not simplify the accepted change. No new store,
framework, lifecycle verdict publisher, or per-task bulk probing was added.
Independent accepted-fix verification: final PASS recorded below. Runtime acceptance remains future
implementation work; this document does not claim the proposed behavior is live.


Cross-family verification round 1 (Claude, October 15): BLOCKED, no P1s.
Reviewed draft SHA-256:
`f8d5b8f38c8e252742719c981697550874fc0e3ebf353565b2e399eb7651f5fd`.
The review verified O3/O4 and the main O1 mechanism. Findings below quote its
labels; suggested remedies were checked against code before disposition.

| ID | Reviewer finding (verbatim) | Disposition |
|---|---|---|
| P2-1 | There is no single validator for "validated terminal projection" that core code can use. | Accepted: §3.1 owns both validators in core/task_evidence.py and forbids duplicate command-layer validation. |
| P2-2 | The rule about racing writers contradicts how TaskMonitor actually decides death. | Accepted ambiguity; declined removing the writer guard. §3.2 now holds standard ctrl_out/outbox while any newest valid snapshot remains, including terminal rows, relying on LivenessMonitor retirement. No Monitor probes or global active-TID-policy change. |
| P2-3 | The new anchor reads during selection have no limit and repeat every slice. | Accepted: bounded acquisition after cheap classification, budget accounting for deferred families, one transient continuation cursor, and a deferred-prefix progress test. |
| P2-4 | Redacting the whole `metadata` object makes it impossible to tell whether a task is a pipeline, and the existing helpers quietly guess a default. | Accepted: validate parent objects, exact booleans, and pipeline status route; no permissive helper reuse during discovery. |
| P2-5 | The final gates change concurrency for the consumer test roots and require a PostgreSQL run that isn't supported. | Partly accepted: supported core PostgreSQL gate and separate consumer roots. CI's serial consumer jobs are real; lack of CI PG coverage does not prove all consumers lack PG support. Declined prescribing reduced-concurrency acceptance, contrary to [ALT-WT-017]; record the matrix difference and preserve default-concurrency gates. |

Session reviewer inventory: Claude is Tier-1 review-eligible, with successful
liveness and repository write-containment probes in this session. The earlier
October 14 authentication failure is historical. Review used a 540-second bound,
plan mode, and Read/Grep/Glob tools; exit 0. No repository implementation changes
were made. Accepted-fix verification of this round is recorded in the subsequent passes below.


### Whole-plan craft and algorithm review (October 15)

Owner requested analysis of the entire amended plan for expert Python craft,
known algorithms, brittleness, overengineering, and unnecessary negative tests.
The design rationale in §1 now states the causal model and the limit on new
abstractions. §6 replaces the exhaustive-field-failure wording with independent
valid examples, invalid equivalence classes, and owner-level behavior tests.
It explicitly avoids combinatorial matrices, duplicate adapter tests, private
call-order/source-text assertions, and test-only production interfaces. Necessary
negative checks remain tied to concrete forbidden effects and reachable paths.
Existing settlement and cleanup machinery remains the implementation base.
Independent whole-plan review and its accepted fixes are recorded below.


Cross-family verification round 2 (Claude, October 15): P2-1, P2-2, P2-4,
and P2-5 PASS; the cursor in P2-3 is implementable. Reviewed draft SHA-256:
`ab160992828f26e3fe0b9e2c920f625e873a7c4ad8a923d2fecfe9a951ed7985`.
New finding P2-6 (verbatim): "Counting deferrals against the budget leaves
cleanup in permanent catch-up mode (introduced by the P2-3 fix)". Accepted:
§3.2 now separates slice budget from completed-pass state, returning a deferred-only
full pass to normal discovery cadence. The regression checks that observable
cadence, not internal cursor calls. No injected I/O callback is needed: bounded
acquisition stays in the owner and supplies values to pure policy functions.
The whole-plan craft review includes verification of this correction.


Whole-plan cross-family review (Claude, October 15), draft SHA-256
`50ede13dc9d03163d6fbe28f7bb25be405998ebe290c43ed23142238c6414450`:
the expert-Python and algorithm lenses support the design; BLOCKED on P2-A.

| ID | Reviewer finding (verbatim) | Disposition |
|---|---|---|
| P2-A | Held families in the Monitor-record lanes must be narrowed, not left pending | Accepted: narrow eligible queue sets and record existing partial-cleanup progress, preserving the existing retained-queue handoff rather than adding another cursor or SQL lifecycle. |
| P2-B | Drop the terminal projection as a Monitor-side retention anchor | Accepted simplification: LivenessMonitor owns snapshot age; Monitor uses presence alone, then local completion/existing Monitor anchors when no hold remains. |
| P2-C | The interactive-shutdown test bullet asks for tests that would pass on the old code | Accepted: add only changed acquisition cases; reuse existing ACK/deadline/cancellation coverage. |
| Wording | Cadence also follows `PolicyProgress.waypoint_reached`. | Accepted: derive traversal signals from one pass-incomplete fact. |
| Optional | Routes stored twice | Accepted: the canonical config replaces independent materialization route copies. |

The reviewer judged the existing due heap, generation checks, shared config,
pure validation, cyclic recordless scan, authority ordering, and inventory model
well matched to the code. Removed duplicate ownership rather than adding a new
abstraction. Targeted verification of these final amendments: PASS, recorded below.


Final accepted-fix verification (Claude, October 15): **PASS, no P1/P2 findings**.
Reviewed draft SHA-256:
`bd4747002aedd92d133c5d81667ed52edccb10a76fb6dab2ddcee7bbffb863ff`.
The reviewer verified record-backed partial progress and eventual handoff to
recordless cleanup, removal of duplicate projection-age logic, reuse of existing
shutdown tests, cadence signals, and canonical route storage. Subsequent wording
clarifies that the deferred-only cadence paragraph concerns the recordless lane
and that Little's Law uses mean retained lifetime, including cleanup delay.
These clarify the reviewed decisions rather than changing runtime scope.

Theory possession probe [WT-7]: the reviewer placed a proposed durable per-TID
route index outside this design, rejected it under [ALT-WT-019]/[ALT-WT-020], and
predicted wrong-clock deletion, stale cleanup decisions, and guessed routes as
the failure classes this plan must prevent. The holistic review supported the
single-writer view, conservative reclamation, existing deadline heap, bounded
cyclic scan, and retained-population model.

Historical verification for the planning-only revision: 4 plan-metadata tests passed at repository
default concurrency; relative document links resolved; targeted `git diff --check`
passed. Only this plan was edited by this task. Specs and runtime code were not
changed, no commit was made, and no runtime/performance acceptance is claimed.
That planning-only revision remained draft; implementation was subsequently
authorized on October 15 as recorded below.


## 9. Implementation execution log (October 15)

Class 5, hardening applies. Owner authorized the complete implementation and
specified three watchouts: synchronize [OBS.13.7]/[LIVENESS.R7] with whole-family
nonterminal protection and terminal-inclusive standard-result holds; preserve the
intentional abrupt-crash difference between bulk inventory and point/explicit
history reads; keep supervisor-only recordless completion on the TID-age fallback.
Existing concurrent documentation/coalescing edits are preserved.

Outcome checklist:
- [x] Promote the reviewed contracts and implementation ownership/backlinks.
- [x] Publish independently valid task projection/config and terminal-only retention.
- [x] Preserve queue cleanup with independent clocks, bounded fair progress, and final revalidation.
- [x] Migrate inventory, point/result/control, interactive and client/CLI observation.
- [x] Reconcile tests, traceability, both brokers, consumer roots, and independent review.

Source-of-truth sections are listed in this plan's header and §4. Program theory
[ALT-WT-019]/[ALT-WT-020] governs placement; no route index or Monitor read fallback.
TaskSpec immutability, task identity, terminal precedence, mode-aware settlement,
reserved policy, and sole-deleter liveness authority stay intact. Specs were
promoted first in a separate owned slice; readers and publisher work proceeds
against that promoted working-tree contract. Parallel ownership: spec/inventory,
publication/liveness, Monitor cleanup, command point/result/event acquisition.
Dependent integration checks and edits remain sequential at each owning seam.

Initial RED evidence: `tests/commands/test_task_owned_observation.py` failed on
unresolved control not raising and unresolved result returning missing. After
reader migration both passed. Existing interactive/settlement regression run
passed 107 cases at default concurrency after fixing nested borrowed-session
ownership and unstable completion source selection. No lower-concurrency gate
was used. More verification and independent reviews are pending.


Implementation review round 1 (Claude, October 15): BLOCKED on two P2 findings.
Accepted R-I1: terminal-seen Monitor records still took the old live-service-only
skip before reporting, so a nonterminal task snapshot could cause repeated
pre-delete reports even though the final guard prevented deletion. Use the same
whole-family protection before reporting and at deletion. Live-code tracing also
showed the limited terminal-record query can hide later eligible work behind
protected rows. The prior plan's "no record-backed cursor" assumed only
retention holds, whose eligible queue set can be narrowed and marked processed.
That assumption fails for whole-family holds, which cannot truthfully be marked
processed. Approved implementation refinement: bounded keyset pagination with
one transient `(last_message_id, tid)` waypoint through the existing worker
handoff. No durable index/schema/lifecycle is added; the completed-pass cadence
rule applies. A large global NOT-IN exclusion and an unbounded prefix scan were
rejected as less robust implementations.

Accepted R-I2: remove the old test-only dead-task pure selector and unused
retention parameters. Preserve meaningful fairness/progress proof at the actual
worker interface. Accepted craft refinements: cache immutable observation from
resolved routes, avoid duplicate initial snapshot publication, keep descriptor
construction inside nonfatal observation behavior, cache the retention config,
strictly reject malformed discovery roles, and use real redaction/reader-parser
and generation-change proof. Snapshot/destructive ownership and independent
retention clocks were verified by the reviewer. Accepted fixes require a
subsequent independent verification.

Synthetic retained-inventory experiment (real broker/policy paths, 500 terminal
rows, standard worker configuration) passed on SQLite and PostgreSQL. Population
was 0→500→500 after stale authorization before expiry→0 after expiry. Across 100
pre-expiry scheduler turns there were no repeated target probes; admission usage
stayed 2→2 and no endpoint owner was reported live. Median bulk inventory changed
10.137→23.332 ms on SQLite and 144.086→218.008 ms on PostgreSQL. Raw scan,
liveness reconcile, admission, and expiry-delete timings are recorded in
`/tmp/weft-observation-inventory-sqlite.json` and
`/tmp/weft-observation-inventory-postgres.json`. These are synthetic namespace
measurements with controlled proof/time, not task-throughput or hard storage
bounds. They support retaining the existing scan without adding a cache/index;
production cost remains proportional to mean retained inventory.

The initial full-fast gate was interrupted for accepted review corrections after
2,217 passing tests, 18 skips, and two stale test_run_public fixtures referencing
a removed helper. That interrupted run is diagnostic evidence, not acceptance.
Full checks will be rerun after the corrections.

Full-fast integration gate, first completed run: 30 failed, 5,718 passed, 37
skipped. Failures expose old implicit-log fixtures, removed-helper fault probes,
expected bulk runtime probing, exact worker handoff fields, suppression inventory
and test classification metadata, plus an intentionally suppressed parent
publication used before bare-TID control. These are being reconciled against
the promoted contracts, preserving borrowed-connection, cancellation, point
runtime, and recovery assertions. No concurrency or timeout reduction is used.
A no-route terminal projection test was sensitivity-checked: removing the
configuration guard wrongly settled a finite-timeout result (RED); restoring
the guard passed all four task-owned observation cases.

Additional keyset verification found a real merge-order defect: a full newer
terminal batch could prevent acquiring an older retained-open batch. The
approved repair acquires bounded candidates from both sources, merges in the
same key order, and applies the final family limit afterward. A real-store
regression must fail on the prior selection and pass on the repair.

Final cleanup refinement: store-side occupancy filtering could still skip
unacquired retained-open residue beyond an empty prefix. A real-store
reproduction established the fault. The final implementation merges two bounded
raw SQL candidate batches in `(last_message_id, tid)` order before limiting;
the existing worker owns queue-presence checks, deadlines, empty-work skips, and
the continuation. This removed the store-side broker-probe/deadline machinery
rather than adding another cursor or callback. The owner regression is RED on
the old selector and GREEN through fresh worker invocations and the real
detached-diagnostics cursor handoff. Empty families emit no cleanup report.
The second full-fast attempt was intentionally interrupted for this correction
after 2,218 passes and 18 skips; it is not an acceptance gate.

Independent full-change review attempt: Claude read-only invocation reached its
540-second bound without a report. It supplies no verdict. Final review was
split into bounded cross-family core/publication/cleanup and public-reader/adapter
units, with the operative plan contract and accepted findings supplied to each.
Both require written verdicts; no same-family fallback has been substituted.

Final frozen-tree fast gate: 5,744 passed, 37 skipped, 203.98 seconds at
repository-default concurrency. The skips are existing backend/platform gates;
PostgreSQL-specific cases are covered by the required supported wrapper next.
Full mypy passes (476 source files), Ruff passes, and whitespace checks pass.
The final cleanup gate also covers real store/worker/maintenance/recovery paths.
No new suppression exemption was added: three obsolete groups were retired and
the exact suppression inventory/index regenerated. The new core parser test
module is explicitly shared.

Cross-family core review (Claude, final bounded Slice A): no blocker, with
accepted corrections before completion. R-IIA-F1: omit blank optional errors
from published terminal projections so a blank exception cannot invalidate
terminal evidence or its retention floor. R-IIA-F2: start the per-family cleanup
deadline after acquisition, matching the other cleanup lanes, so slow inventory
acquisition cannot consume every turn without progress. R-IIA-F3/F4: skip empty
standard families before evidence reads and avoid rewriting existing cleanup
marks; synchronize the SQL ownership docstring. R-IIA-F5: remove the unused
constructor override and policy fallback that could merge retention clocks;
cache service configuration once and require the independent policy clock.
R-IIA-F6: replace per-update O(N) heap rebuilding with standard lazy invalidation.
The retained population magnifies this pre-existing update cost, so this
algorithmic correction is within the plan's performance acceptance. It adds
no durable index or scheduler framework. Accepted fixes need firing tests and
a separate verification pass. The full-slow gate was interrupted for these
corrections after 4,110 passing tests and 28 skips; it is not acceptance.
The reviewer confirmed first-round R-I1/R-I2 and final raw candidate merge,
terminal/probe separation, task-only recordless anchors, and generation/custody
revalidation. No tests were run by the reviewer.

Cross-family public-reader review (Claude, bounded Slice B): no blocker. Accepted
R-IIB-F1: pure point queries need no wakeup subscription; use a plain borrowed
connection and resolve control configuration once per TID, retaining subscriptions
for actual waits. [PY-2] now states this precise boundary. R-IIB-F2: remove dead
realtime history plumbing and ignored private parameters, retaining explicit
raw-history APIs. R-IIB-F3/F4: classify only acquired metadata with no hidden
rediscovery; preserve acquired task projection above a later supervisor verdict.
R-IIB-F5/F6: rebind status-watch interests when custom routes appear, and check
cancellation immediately after yielding a snapshot. R-IIB-F7/F8/F9: close
interactive input before route discovery can fail; emit an unknown realtime
snapshot when no evidence exists, matching [DJ-11.1]; exercise unresolved control
through the public typed command. Pre-existing shared non-completion control
handling, CLI watch consumption, and duplicate explicit history replay remain
observations outside this slice, not silent contract changes. The reviewer ran
no tests. Accepted fixes need focused firing proof and independent verification.

Independent accepted-fix verification: Claude core F1–F6 PASS and public-reader
F1/F3–F9 PASS. The reviewer verified the real race tests, lazy heap invalidation,
raw keyset merge, separate clocks, family-budget placement, and point-query/wait
subscription boundary. A final orphan cleanup removed the unused history
injection parameter, its orphaned fold/acquisition helpers, and the unreachable
legacy source-label conversion. Two loan-only cases had no production owner
and were removed; real explicit-history body filtering, imported history, and
borrowed connection cases remain and now call the actual explicit reader.
The final small-cleanup verification also returned PASS with no new defects.
All reviewers report reading code only, not running tests.

Non-actionable P3 dispositions: existing unit-level scheduler-presence assertions
remain; no heap-tuple layout assertion was added. Per-pass nonstandard-family
metrics and a bounded duplicate discovery read while status-watch routes remain
unresolved are retained to avoid extra plumbing without a demonstrated product
fault. Three route-name copies within `tasks.py` now use its existing helper.
The result observer's adjacent helper has a distinct history/uniqueness interface;
constructing a result materialization solely to share queue-name lines was
declined as greater coupling. The reviewer confirmed that disposition.

The final completed fast run before the last fixture reconciliation had 5,747
passes, 37 skips and three failures, all stale expectations that realtime would
emit no initial snapshot. Foreign cancellation still requires a real queue wake;
adjacent imported stream IDs still both render. Those assertions are retained
after consuming the required unknown snapshot. All 133 affected tests passed.
The full gate sequence is being repeated on the final frozen source.

Final SQLite gates completed on the frozen runtime: fast 5,750 passed/37
skipped; slow-enabled 5,751 passed/48 skipped. Full PostgreSQL first attempt
reported one test-fixture failure after 2,095 passes and 12 skips: the watch
fixture logged running but serialized a canonical TaskSpec still in created
state. The snapshot view correctly displayed created. Set the fixture's state
to running; preserve its revision freshness and zero-new-connection assertions.
No production source changed, and the fixture is PostgreSQL-only, so the SQLite
acceptance remains applicable. Rerun the original PostgreSQL workload and
worker policy; no concurrency or timeout reduction is authorized or used.

### Completion evidence

Completed October 15. All required commands ran at their original repository or
supported-wrapper worker policy. No production timeout, test concurrency, or
workload was reduced. Final results:

| Gate | Passed | Skipped |
|---|---:|---:|
| Core fast (SQLite) | 5,750 | 37 |
| Core slow-enabled (SQLite) | 5,751 | 48 |
| Supported PostgreSQL `--all tests` | 5,701 | 27 |
| Django integration | 163 | 1 |
| Docker extension | 113 | 0 |
| macOS sandbox extension | 26 | 0 |
| Microsandbox extension | 77 | 0 |

Skips are existing environment/backend gates, including opt-in live providers
and Django's PostgreSQL-target proof in its separate SQLite consumer run. New
shared observation/retention contracts ran on both core backends. PostgreSQL
containers created by these runs were cleaned up; unrelated older containers
were preserved. Canonical mypy passes on 476 files; Ruff and suppression-index
validation pass. The changed Django test module also passes mypy with its
existing fixture-project import root supplied. Whitespace checks pass.

The final Django consumer initially exposed two old reject-any-event disconnect
probes. They now consume exactly one required unknown snapshot before the idle
wait. All original direct-wake, indefinite-wait, owner-thread closure, and
no-read-after-cancellation assertions remain. Independent Claude review PASS
confirmed those contracts. An additional assertion rejects a snapshot arriving
after the cancellation wake; the full 163-case consumer run passed again.
No production code changed for either the PG fixture or Django probe repairs,
so earlier runtime gates remain applicable.

Independent review closure: first-round R-I1/R-I2 verified; core R-IIA F1–F6
PASS; reader R-IIB accepted fixes PASS; orphan cleanup and final Django probe
verification PASS. No accepted finding remains open. Reviewers did not run
tests; the execution evidence above is the author's gate evidence.

Final backstitch: 31 errors, 1,110 warnings, 676 infos versus HEAD baseline
31 errors, 1,107 warnings, 677 infos. Normalized comparison introduces no errors
or infos. The five new warnings are unrelated concurrent coalescing edits to
retired-plan reference wording; two old warnings disappeared. Existing debt is
not claimed clean and was not changed by this slice. Concurrent documentation
and coalescing work is preserved. No commit or release was requested or made.

Promoted spec working-tree identities (including preserved concurrent edits):

| Promoted working-tree spec | SHA-256 |
|---|---|
| `docs/specifications/00-Quick_Reference.md` | `5d93b9675b0e653d12abbed0b911105dd731b4db01ff6c53e55014d6f0334051` |
| `docs/specifications/01-Core_Components.md` | `ace058051252df9181320b0fe48b54c49f227d2abc7a10d541b83596cb7f2484` |
| `docs/specifications/04-SimpleBroker_Integration.md` | `9f866971e25bff8e07cc28e2d92fb206737baa37ffabdf53725b681fa19cc893` |
| `docs/specifications/05-Message_Flow_and_State.md` | `76dcd22489530da1f0ee2a0e7cc0a6e23b79432e9d5528e63e147614f8d2c31d` |
| `docs/specifications/07-System_Invariants.md` | `a93a95d21a9a3b1a592a557ebe4aa89990badb88a6bf463d2d15d1193292d215` |
| `docs/specifications/10-CLI_Interface.md` | `07961c10cd3cbbb3cf306f88870dfc376491a72de5d1c063f2c2ecce4bbe7095` |
| `docs/specifications/13C-Using_Weft_With_Django.md` | `6c693eec5ff8648b8451e3c840c900d0b9a68da16a169406bfee9a319c5a39b9` |
| `docs/specifications/14-Python_API_Surfaces.md` | `638a3a883db78df7293ec9477ad9a68676905d73a5dd0f08abca0038990678fa` |
