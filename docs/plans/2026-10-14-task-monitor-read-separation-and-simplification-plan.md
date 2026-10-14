# Task-Owned Observation and Read Separation

Status: draft
Source specs: docs/specifications/00-Quick_Reference.md [QR-4]; docs/specifications/01-Core_Components.md [CC-2.2], [CC-2.4], [CC-2.5]; docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-5], [MF-6]; docs/specifications/07-System_Invariants.md [OBS.6], [OBS.6a], [OBS.13], [LIVENESS.R2], [LIVENESS.R3], [LIVENESS.R6]; docs/specifications/10-CLI_Interface.md [CLI-1.2], [CLI-1.3]; docs/specifications/14-Python_API_Surfaces.md [PY-2]
Superseded by: none

Class: 5. This proposes a public observation contract, a task-state payload,
retention policy, and command/client read-path changes. Hardening applies.
Plan type: implementation with spec revision. **Planning only: no spec promotion
or implementation is authorized by the request that produced this revision.**
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

## 2. Baseline and required reading

Review baseline: Git `9b7ada69a21474346b44700d31157819819e62a6` plus the
October 14 working-tree versions identified below. Other work was modifying
family scheduling concurrently. These hashes identify the read baseline, not a
claim that HEAD contains these contents. Compare `git diff <baseline> -- <path>`
and current hashes before implementation; rebase overlapping decisions rather
than reverting another slice. Promotion baseline: not yet created.

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
`events.py`, and `result.py` own acquisition and settlement. CLI adaptation is
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
not a pluggable resolver or a second snapshot class hierarchy.

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
to retain this value instead of manufacturing a partial TaskSpec. Retain full
accepted TaskSpecs where their existing callers need them. Convert at the
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
bounded diagnostic edges. Poll reports and manager child reports alone do not
republish state. Preserve the last display event in owner-local state. Failed
publication stays nonfatal, warns once per consecutive failure run without
payloads, and retries on the next owner edge. Do not use `max_messages=1` or
read-before-write comparison.

Add `WEFT_TASK_OBSERVATION_RETENTION_SECONDS` in `weft/_constants.py`, default
172800 (48 hours), finite and at least `LIVENESS_MAPPING_MIN_AGE_SECONDS`
(currently 2400). It is the minimum retention for the newest liveness-valid
snapshot and for standard terminal `ctrl_out`/outbox evidence. It is separate
from raw-log retention. Superseded/malformed task-state history keeps the
existing 2400-second rules; a malformed nested projection cannot shorten the
newest-valid row's floor. LivenessMonitor alone still probes and deletes exact
IDs, verifies older valid rows absent, and protects Manager launch custody.
Age alone never permits deletion of a live or otherwise protected newest row.

Newest-snapshot age is measured from its broker row ID under the existing age
policy; terminal result/control age uses existing terminal-evidence age. Normal
terminal publication therefore retains routing for at least the standard
result window. A missing final publication, abrupt death, administrative
pruning, or dump/load (which excludes runtime state) cannot promise a fresh
completion-based window. State this limit; never synthesize a terminal outcome
or renew a row on behalf of a dead task. Custom/shared queues remain outside
automatic cleanup and may outlive route discoverability. Reserved-policy and
reserved-cleanup age protections are unchanged.

The retention wiring is part of this read contract, even though some owners
live under Monitor. Add the validated observation-retention value to
`TaskMonitorRuntimeConfig.from_config` and the LivenessMonitor configuration
path, loaded through `_constants.py`. Keep distinct parameters rather than
reusing `mapping_min_age_seconds` for two policies:

| Owner | Required clock/action |
|---|---|
| `LivenessMonitor._read_mapping_history` / `reduce_mapping_history` | Existing history minimum, including malformed rows; unchanged. |
| `LivenessMonitor._apply_probe_result` newest-row retirement | Observation-retention minimum plus all existing probe/custody/generation checks. |
| `runtime_control.terminal_task_runtime_queue_cleanup_plan` and `stale_service_owner_runtime_queue_cleanup_plan` | Observation clock for standard completion/outbox; existing immediate-control rules unchanged. |
| `dead_task.dead_task_queue_cleanup_plan` | Separate observation clock for ctrl_out/outbox from existing reserved clock. Preserve name-derived age fallback and every proof/preservation rule. |
| `runtime_control.runtime_dead_task_record_probe_tids`, `select_runtime_dead_task_cleanup_candidates`, `_has_retention_deferred_only_work` | Carry both clocks through candidate/probe/deferred selection, using the relevant queue role, so selection cannot bypass or indefinitely defer the actual cleanup rule. |
| Monitor store summary/parent age, raw external output, reserved workers | Retain current clocks. Keep reserved default derived from configured log retention and its explicit override. |

Update `MaintenanceWorker` call sites and cached configuration diagnostics in
`task_monitor.py`. Do not globally replace the old retention key. Test unequal
clocks in both directions, absent Monitor records, and invalid/nonfinite config.
The observation floor does not create terminal proof where none exists.

### 3.3 Reader matrix and explicit capability changes

| Surface | New contract |
|---|---|
| `status`, `task list`, stats, default Python status/list | Current nonterminal projections; one namespace scan and shared broker scope; no task-log, Monitor-store, per-task control/outbox, or active PING reads. |
| `--all` / `include_terminal=True` | Include retained terminal projections from the same inventory; not a union with retired history. |
| `--log` / `include_log_history=True` | Explicit retained raw-log reconstruction unioned with current projections; terminal task proof wins, otherwise valid current projection wins. No Monitor tables or external-log reads. An empty retained log is an honest empty historical contribution. |
| Known-TID status/result/control | Accepted configuration or validated task-state observation metadata; configured local queues and current runtime evidence. No implicit global-history or Monitor-store fallback. |
| Task event/watch APIs | Raw lifecycle following remains explicit and best effort while rows are retained. Local completion still ends result-following; do not invent missing intermediate events from snapshots or promise lossless observation against a destructive monitor. |

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

For unresolved bare TIDs, register task-state interest and a read-only standard
`T{tid}.ctrl_out` discovery probe before reading. A standard control envelope
may supply configuration only if task-authored, exact-TID bound both outside
and inside its TaskSpec, and all required immutable fields validate. A redacted
or incomplete TaskSpec is not configuration. Never default missing fields to
one-shot or claim output from guessed queues. Once routes resolve, subscribe to
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
projection cannot outrank local terminal proof. Snapshot projections do not
invent work-item completion records.

## 4. Proposed Spec Delta

Apply these exact paragraphs in a future **spec-promotion slice**, before code.
They replace conflicting sentences, not append exceptions beside them. Add this
plan's backlink then. Do not promote specs during this planning request.

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
> task-authored standard completion containing complete immutable configuration.
> A standard completion queue is only a read-only discovery candidate, never
> permission to assume default transport. Register queue interests before reads
> and register discovered routes before consuming output. Missing configuration
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

> Newest liveness-valid rows additionally satisfy the configured task-observation
> retention minimum. Superseded and malformed rows retain their existing minimum
> age. Probe authority, unknown-timeout policy, launch-custody protection,
> generation revalidation, and exact-ID deletion ownership do not change.

### `10-CLI_Interface.md` [CLI-1.2], [CLI-1.3] and `14-Python_API_Surfaces.md` [PY-2]

> Status/list default to current nonterminal task snapshots. `--all` includes
> retained terminal snapshots; `--log` explicitly adds retained raw history and
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
> finite values at least 2400, and governs newest-valid task snapshots and
> standard terminal result/completion retention. It does not change raw-log or
> reserved-message retention and is not an exact expiry or an audit guarantee.

At promotion remove the contrary terminal Monitor fallback text in [MF-5] and
[OBS.13], including implementation mappings; preserve its private monitor uses.
Reconcile [WT-2]/[WT-4] theory terminology, but do not invent nonexistent theory
section titles. The theory's queue-only direction is preserved, not relaxed.

## 5. Implementation slices (future authorization required)

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
   `policies/dead_task.py`, `task_monitor.py`).
   Reuse existing redaction, exact-ID reaper, task-state reader, and terminal
   latch. Update Manager/TaskMonitor overrides only to preserve extensions.
   Verify normal completion, failed publication, newest-invalid nested data,
   young/old history, late newer row, live old row, and launch custody.
3. **Current inventory and service view.** Files: `commands/system.py`,
   `commands/_task_snapshot_reducer.py`, `commands/tasks.py`, `cli/app.py`,
   `client/_namespaces.py`. One collector; separate explicit history acquisition
   before reduction. Verify no ordinary log/Monitor opens, filtering/stats,
   client parity, redaction, retained terminal rows, and service launch gaps.
4. **Known-TID and control acquisition.** Files: `core/task_evidence.py`,
   `commands/_task_history.py`, `_result_wait.py`, `tasks.py`, `events.py`,
   `result.py`, existing client handle adapters and command error/rendering
   owners. Replace all implicit history/Monitor acquisition, including
   `known_tid_evidence`, `_await_control_surface`, `task_ping`, and ctrl route
   helpers. Keep the existing settlement algorithm. Remove obsolete helpers
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

Use real `WeftTestHarness`, broker queues, store transactions where needed, and
real task publication. Fault injection belongs at OS/export/broker-error edges,
not at the reducer under test. Add cases to existing
`tests/tasks/test_task_observability.py`, `tests/tasks/test_liveness_monitor.py`,
`tests/core/test_task_state.py`, `tests/commands/test_task_evidence.py`,
`tests/commands/test_status.py`, `tests/commands/test_task_commands.py`,
`tests/commands/test_result.py`, `tests/commands/test_result_settlement_policy.py`,
`tests/commands/test_result_activity_waits.py`, `tests/core/test_client.py`, and
`tests/cli/test_status.py`. New boundary tests may live in `tests/architecture/`.
The architecture assertion targets observation imports/access to monitor
store/sql; `commands/task_monitor.py` legitimately invokes administrative monitor
capabilities and must not be prohibited by a blanket `core.monitor` ban.

Required firing cases: each projection/descriptor field and validation failure;
redaction; retained unknown rows; custom/shared queues; interactive and streaming
persistent batches; pipeline status override; accepted-handle prelaunch;
bare-TID prelaunch with finite timeout; no-timeout unavailable; metadata expiry
while waiting; restart/dump-load without metadata; malformed/mismatched/redacted
standard completion; supervisor-vs-task precedence; terminal projection with absent completion;
conflicting task completion/projection; terminal projection without routes; no output claim or control
write before configuration; every new flag and config key. Test both `--all`
and `--log` with an empty raw log. Assert the unknown result exit is 1, the
observation timeout is 124, and usage errors retain the existing mapping.

Per-slice commands (standard concurrency, no acceptance `-n 0`):

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/tasks/test_task_observability.py tests/tasks/test_liveness_monitor.py tests/core/test_task_state.py -q
./.venv/bin/python -m pytest tests/commands/ tests/cli/test_status.py tests/core/test_client.py -q
```

Final implementation gates:

```bash
. ./.envrc
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
git diff --check
```

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
staging; this planning request does not authorize a commit.

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
