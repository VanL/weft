# TaskMonitor Ingest and Retirement

Status: draft
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-6]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4a]
Superseded by: none

Class: 5. This changes durable telemetry handoff and destructive cleanup policy.
Plan type: implementation with spec revision. Planning only; no implementation
or spec promotion is authorized by the request that produced this revision.
Promotion strategy: A, requirement text first, then synchronized implementation
mappings and code backlinks in each owning slice.

## 1. Goal and prerequisite

Let TaskMonitor consume valid raw telemetry as soon as it has durably folded
that telemetry. Raw deletion must not depend on task termination, lifetime-report
emission, or catching up to the global log head. Retain the existing recovery
proofs and keep report finalization separate from raw ingestion.

The [read-separation plan](./2026-10-14-task-monitor-read-separation-and-simplification-plan.md)
is a hard release prerequisite: ordinary status, result, control, and client
reads must work without raw logs or Monitor tables before enabling this policy.
Its public observation contract stands on its own; it can ship without this
plan. Do not implement both as one unreviewable rewrite.

This follows `docs/program-theory.md` [WT-2], [WT-4], [ALT-WT-009], and
[ALT-WT-012]: operational facts belong to task queues; telemetry is consumed
work. The private Monitor store is an export/reclamation work area, not a second
operational lifecycle authority. It can retain necessary pending work without
becoming an ordinary-reader database.

The strongest argument for retaining raw rows until external export is that
raw rows remain an independent recovery copy if the private store is lost.
This proposal accepts a narrower telemetry guarantee: after a successful fold,
that copy may be deleted. The store and deferred-export records must therefore
be preserved through restart/upgrade. No task execution, result availability,
or task-owned terminal authority depends on this tradeoff.

## 2. Baseline, sources, and actual owners

Baseline Git `9b7ada69a21474346b44700d31157819819e62a6`, with these October 14
working-tree inputs (concurrent family-scheduling work must be preserved):

| File | SHA-256 |
|---|---|
| `weft/core/monitor/task_monitor.py` | `de561ea079c8c870ade9e5a3efb683583c80c7bd2c51184ddce1bc2780de04b4` |
| `weft/core/monitor/store.py` | `8116a45d4706c5ce5e2b41e3729e8524f90836cd5aa8b36bfaa5057bd080ed75` |
| `docs/specifications/05-Message_Flow_and_State.md` | `aa0612b5c66679a090ec744ee150a1e94a4395625fec28d6c4b93f4525ad7c7e` |
| `docs/specifications/07-System_Invariants.md` | `89efe72c64842f6bab4d00913e4cf494c8dd1fef9057d15fb6de9d0729d0587a` |

Reconcile current hashes/diffs before implementation; record a spec-promotion
baseline before code. Read the source specs, program theory, engineering
principles, lessons (especially May 31 checkpoint recovery), and writing-plans,
hardening-plans, review-loops-and-agent-bootstrap, and testing-patterns runbooks.
The September MaintenanceWorker plan explains the current execution owner;
older Monitor plans explain history and are not a backlog to execute.

`MaintenanceWorker` in `core/monitor/task_monitor.py` owns
`_run_monitor_store_cycle`, `_ingest_retained_task_log_rows`, exact deletion,
pre-checkpoint/orphan repair, and report handoff. TaskMonitor owns dispatch,
result application, and cached diagnostics; retain its owner-thread boundary.
`store.py` owns transactions, collation families, exact raw-message references,
checkpoints, and deferred reports. `sql.py` constructs their queries.
`lifetime_report.py` projects reports; `external_log.py` owns the sink.

Actual defaults are monitor mode `delete`, raw-log/terminal retention setting
172800 seconds, selection batch 5000, scan limit 50000, and store write batch
100. External lifetime-report schema is 2; private store schema is 6. Do not
change defaults, schema versions, or error types just to match an old plan.
`ExternalTaskLogError` is the handoff failure boundary, not merely `OSError`.

Current `delete` mode already deletes valid rows immediately after durable
fold. Current `jsonl_then_delete` retains them until summary handoff. The change
is to give both collated destructive modes the former ingestion rule, and to
retry proven exact raw deletions without waiting for a full FIFO pass.
The retention setting is not a promise that folded raw rows survive 48 hours;
make its remaining summary/family/staleness uses explicit in spec/config docs.
The read plan separately owns terminal output/completion retention.

## 3. Design and invariants

### 3.1 Keep the four tables and their distinct responsibilities

Keep collation families, ingestion metadata, raw-message references, and deferred
reports. No table DROP, replacement log, extra watermark, schema migration, or
legacy decoder is included. The child-reference table is an exact-delete retry
ledger, not redundant history. The deferred table is a durable export outbox;
a report there can be the only remaining copy after raw and parent retirement.
It also carries malformed-row and inferred runtime reports that have no
surviving collation. Reconstructing retries from current collations loses data.

Preserve the immutable accepted report body and existing report-ID/retry
semantics. Do not regenerate a deferred report from a later collation. No
exactly-once external-file delivery guarantee is added.

### 3.2 Fold, delete exact rows, reconcile, then advance

Keep the existing protocol; do not introduce a new cursor protocol:

1. Bounded FIFO acquisition returns exact visible raw IDs. Commit each bounded
   `record_task_log_updates(..., checkpoint_message_id=None)` chunk with its
   merged collation and child references in the same store transaction.
2. In collated destructive modes, valid rows whose fold succeeded are eligible
   for exact raw deletion immediately, including nonterminal rows. In
   `jsonl_then_delete`, malformed/unrecognized rows still require accepted
   external-or-deferred raw-report handoff before deletion. Failure of either
   store or required handoff preserves the affected rows and blocks that pass's
   checkpoint. Keep the existing conservative batch failure boundary.
3. Delete selected raw IDs with the existing exact-delete helper. On a short
   batch delete, verify per ID. Reconcile child references only for IDs deleted
   or positively absent; a read/delete error is not absence. Set
   `raw_deleted_at_ns` only through the existing verified reconciliation.
4. Advance the forward checkpoint only after the selected pass has no store,
   handoff, raw-delete, or reference-reconciliation errors. `report_only` keeps
   its existing exception: successful durable ingest can advance without
   deletion. The checkpoint is an ingestion cursor, never a proof that all
   lower IDs were folded or all raw rows disappeared.

Restart after fold but before deletion finds the raw rows and/or durable refs.
Restart after deletion but before reference reconciliation retries the refs and
confirms absence. Restart after reconciliation but before checkpoint resumes
from the old cursor without recreating absent raw rows. Partial store commits
must replay without changing the final collation; verify the existing reducer's
idempotence at this fault boundary rather than assuming transaction atomicity
across all write chunks. Do not add another dedup index speculatively.

### 3.3 Separate raw retry progress from summary completeness

Run `_delete_monitor_store_task_log_rows` once per destructive collated cycle,
with its existing bounded candidate limit, outside the
`completed_fifo_high_water` block. Its call to
`list_deletable_task_log_messages` uses `require_summary=False` in both
collated destructive modes. Existing SQL already supports that selection and
joins references to their durable parent. Do not invent a second candidate
query or broaden it to unrelated rows with IDs below the checkpoint.

Keep full FIFO high-water gating for `_emit_monitor_store_summaries` and the
existing recovery ordering around summaries. A terminal event at a batch
boundary does not prove all later activity/resource rows were folded.
`summary_emitted_at_ns` remains a real handoff marker; it must not be set merely
to make deletion eligible. Early raw deletion is safe because the collation
persists; early finalization could permanently omit later telemetry.

Keep bounded pre-checkpoint and orphan recovery. Imported/backfilled rows below
the cursor are not known ingested. They must be folded before deletion even if
a family already exists. A prior summary is not proof of ingest for a newly
found raw row. Apply the valid-row fold-before-delete rule consistently in
these recovery paths, including removal of the old JSONL summary prerequisite
for valid folded rows. Keep malformed/inferred report handoff protections.
Do not remove recovery to make the loop shorter. Recovery for exceptional
backfilled rows may still wait for ingestion catch-up; this plan does not
promise a global no-pause recovery bound under continuous overload.

Parent retirement still requires all existing proofs: disposition, summary
handoff where required, verified raw deletion, no child references, terminal
control cleanup, reserved probe when needed, and retention. A new fold resets
raw-cleanup eligibility as existing merge logic requires. Never retire a parent
while raw-deletion refs remain. No raw cleanup decision may authorize deletion
of active/ambiguous work, task snapshots, spawn custody, user payload, or
custom/shared task queues.

### 3.4 Retire the separate manager launch-hint compactor

Manager-authored `task_spawned` is included in the valid-row rule. Deliberately
retire the keep-newest-1000 launch-hint guarantee and creation of
`manager_task_spawned_retained_event` reports in collated JSONL mode. The
ordinary manager collation does not preserve per-child `child_tid`,
`child_taskspec`, or `child_pid`; this proposal therefore loses that per-launch
export detail, not just a redundant raw recovery copy. State this change in
README/CHANGELOG. Operators needing raw launch events can select the existing
raw-external telemetry mode; no automatic fallback or new archive is added.

Remove `_trim_manager_task_spawned_task_log_rows` and its invocation, the
sole-use `_manager_task_spawned_lifetime_report` and exact-row fetch helper,
`list_manager_task_spawned_retention_refs`/its SQL, the sole-use
`delete_task_messages_after_event_trim`, and
`TASK_MONITOR_MANAGER_TASK_SPAWNED_KEEP_RECENT_DEFAULT` after a caller sweep.
Normal exact-reference reconciliation now owns these rows, including open
manager families; a raw-deleted marker is only current cleanup progress and
never closes a live family. New folds reset it as usual.

Do not alter Manager publication or launch custody. Requests/reservations,
Manager checkpoints, child runtime state, and service registries keep their
current owners. Read-plan service status already accepts the interval with no
child projection as reserved/unknown. Opportunistic submission/reconciliation
log evidence may also be absent; missing a launch hint must never permit
resubmission or prove child failure. Verify existing submission/custody paths
with hints consumed. This is a regression gate, not permission to redesign
submission. Existing deferred per-launch reports remain immutable and must
still flush after upgrade; retire creation, not delivery of accepted records.

### 3.5 Preserve ownership modes and resource limits

| Configuration | Raw-log owner and action |
|---|---|
| collated `delete` | Fold durably, delete exact valid rows, retry references independently of high-water. Existing malformed deletion policy stays. |
| collated `jsonl_then_delete` | Same valid-row rule; malformed/inferred reports require external-or-deferred handoff. Lifetime report still waits for completeness/disposition gates. |
| collated `report_only` | Ingest/checkpoint as today; no raw, child-reference, or task-family destructive cleanup. |
| `raw_external` | Existing external emit-before-exact-delete path; no collation ingest as a substitute for raw delivery. Its report-only behavior stays unchanged. |

Preserve the four existing public `policy_progress` identities and cached-only
PONG. Extend their existing reason counts only where changed eligibility needs
an explanation. Keep transactions, selections, and retry batches bounded.
Use existing catch-up scheduling; do not add polling or a permanent worker.

This removes a task-lifetime/summary dependency from normal raw drain; it does
not guarantee a size cap. Convergence needs successful storage and cleanup,
fair repeated scheduling, and drain capacity above arrival rate. Sink failure
can grow the deferred outbox; unavailable Monitor storage preserves raw rows;
claimed rows remain outside ingest. Open families and incomplete cleanup may
persist. Surface backlog/error evidence without claiming unconditional bounded
storage or forcibly dropping it.

## 4. Proposed Spec Delta

Promote the following exact replacement text before implementation, with this
plan's backlink. No spec is changed as part of this planning request.

### `05-Message_Flow_and_State.md` [MF-5] and `07-System_Invariants.md` [OBS.13.3]

Replace the JSONL raw-row-until-summary rule and corresponding implementation
notes with:

> Under collated ownership, both destructive modes may delete an exact valid
> raw task-log row after its collation update and raw-message reference commit
> durably. This applies to nonterminal rows and does not await a lifetime
> report. In `jsonl_then_delete`, malformed/unrecognized raw rows and inferred
> runtime cleanup reports still require accepted external emission or durable
> deferred-report handoff before destructive cleanup. Deferred report bodies
> survive independently of raw rows and collation-family retirement. Raw
> external ownership retains emit-before-delete semantics.

### [MF-5], [OBS.13], [OBS.17] checkpoint, recovery, and retirement clauses

> Exact raw-message retry work runs in bounded batches independently of FIFO
> high-water completion. References are removed only after verified deletion
> or confirmed absence; errors preserve retry evidence. Forward checkpoints
> advance after a successful selected pass, with non-destructive ingest-only
> advancement retained in `report_only`. A checkpoint does not prove that
> imported/backfilled earlier rows were folded. Bounded pre-checkpoint/orphan
> recovery folds unknown valid rows before deletion. Summary creation retains
> full FIFO high-water gating and existing retention/completeness rules. Parent
> retirement retains all cleanup, disposition, summary, and no-reference proofs.

### [MF-5] manager-event compaction and [MF-6] launch-hint retention

Replace the keep-newest compaction paragraphs with:

> Manager-authored `task_spawned` rows are optional retained telemetry hints,
> not child lifecycle or launch-custody authority. Under collated ownership they
> follow the same durable-fold-before-exact-delete rule as other valid raw rows.
> No newest-launch-hint set or per-launch lifetime report is retained by the
> collated exporter. Existing deferred reports of this kind remain deliverable.
> Operational service reads use current task/service/custody evidence; explicit
> raw-history reads can enrich a view only while launch hints remain. Absence
> of a launch hint never establishes absence, failure, or permission to replay
> a spawn request. Raw external ownership continues its existing raw export.

This replaces both the [MF-5] private manager compaction-subphase description
and the [MF-6] newest-launch-hints retention guarantee. Align the adjacent
service-observability paragraph with the read plan's ordinary versus explicit
history distinction. Do not remove Manager's launch-event publication contract.

### [MF-5] configuration and ownership notes

> `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS` retains its existing terminal-family
> summary eligibility where applicable, family retirement, reserved,
> stale-open, and raw-external age policy uses. It is not a
> minimum residence time for valid raw rows already folded under collated
> ownership. Standard terminal output/completion and newest task-state retention
> use `WEFT_TASK_OBSERVATION_RETENTION_SECONDS`. Neither clock guarantees a
> complete historical inventory, a storage-size cap, or an exact expiry.

Remove conflicting old sentences in both specs and nearby mappings, especially
“no raw task-log row ... before ... summary_emitted_at_ns”, and the
[OBS.13.3] claim that only age-expired raw rows are ingested. Current collated
code already ingests young rows; the unified fold-before-delete rule is explicit. Preserve summary
completeness, malformed/inferred handoff, and raw-external guarantees rather
than deleting their whole paragraphs. Add the plan link beside the owning
methods' spec references. README/CHANGELOG explain the raw-history loss and the
need to preserve private exporter state. External schema 2 and private schema 6
remain unchanged unless a separately reviewed defect proves a shape change
necessary; this proposal needs no DDL.

## 5. Future implementation slices

1. **Promote contracts and establish failing proofs.** Verify the read plan's
   release gate, then update the exact spec sections and README/CHANGELOG.
   Add broker/store-backed tests for a live task's JSONL raw rows draining
   before summary and for reference retries during partial FIFO passes.
2. **Unify valid-row ingest deletion.** In `MaintenanceWorker` retain ordered
   fold/delete/reconcile/checkpoint calls. Change only eligibility and the JSONL
   malformed report branch so valid rows join selected deletions after durable
   fold. Verify the four ownership-mode rows and crash boundaries in §3.2.
3. **Uncouple raw retries and reconcile recovery.** Move the existing exact-ref
   retry call outside the high-water summary block; use the existing selector
   with summary disabled. Reconcile pre-checkpoint/orphan branches with the
   same valid-row rule. Retain summaries, deferred export, parent proofs, and
   public diagnostics. Retire the manager compactor/report creation per §3.4,
   preserving delivery of existing deferred reports. No unrelated store/API rewrite.
4. **Acceptance and independent review.** Prove restart convergence, unavailable
   storage, exporter outage/restart, backfilled rows, and both backends. Recheck
   read separation and reciprocal spec/code/plan mappings. Review each slice
   and the final diff. Stop if deleting a row requires a new unrecorded proof,
   if a parent/export body can disappear prematurely, or if callers still
   need Monitor tables for normal observation.

Expected code files: `weft/core/monitor/task_monitor.py`; `store.py`/`sql.py`
for removal of the obsolete manager compactor methods/SQL, plus
`weft/_constants.py` for its private keep-count constant. Other store/SQL changes
need a small verified contract repair.
Read `collation.py`, `lifetime_report.py`, `external_log.py`, and exact-prune
helpers; their format and delivery contracts are constraints, not rewrite scope.

## 6. Verification

Use the real broker/store and `WeftTestHarness`; fault only the actual persistence,
exact-delete, or exporter boundary. Prove queue contents, replayed collation,
remaining references, exact deferred payload, and eventual parent retirement.
Use existing tests in `tests/tasks/test_task_monitor.py`,
`tests/core/test_monitor_store.py`, `tests/core/test_task_log_scanner.py`,
`tests/core/test_monitor_external_log.py`, and
`tests/core/monitor/test_lifetime_report.py`.

Keep these existing regression contracts:

- `test_task_monitor_skips_terminal_summary_after_partial_fifo_pass`: later
  activity beyond a batch boundary prevents a premature final report.
- `test_task_monitor_jsonl_then_delete_defers_external_failure_and_deletes`:
  exporter restart must deliver the exact saved report after raw and parent
  retirement. This is why the deferred table cannot be dropped.

Add firing cases for partial store chunk commit; crash after fold, after raw
exact deletion, and after reference reconciliation; under-delete and uncertain
read; no new raw rows but outstanding refs; terminal/nonterminal JSONL valid
rows; malformed report sink and store failures; report-only and raw-external
ownership; positive/zero family retention with fixed hybrid IDs; live/claimed
work exclusion; below-cursor imports; and no premature parent retirement.
Exercise existing append-only schema-6 data through upgrade/restart without
DDL or metadata loss. Replace old manager keep-count/report-creation tests with
observable consumption of all valid launch rows, no new per-launch report,
unchanged open-family lifecycle, preserved existing deferred launch-report flush,
and submission/custody behavior after hints disappear. A drain test must state arrival/drain assumptions; do
not encode a false hard-size assertion or use timing sleeps as its oracle.

Per-slice commands (normal configured concurrency):

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py tests/core/test_monitor_store.py tests/core/test_task_log_scanner.py tests/core/test_monitor_external_log.py tests/core/monitor/test_lifetime_report.py -q
```

Final implementation acceptance uses the full pytest, slow/backend, mypy, ruff,
and traceability gates in the read plan §6, plus the read-boundary regressions
again with raw logs drained and store lookup unavailable. Plan-only checks do
not satisfy runtime acceptance. Run tests under both SQLite and PostgreSQL
through repository backend fixtures; no acceptance serialization or hidden
backend skips. Record commands, environment, failures, and repaired findings.

## 7. Rollout, rollback, and review

Ship only after read separation is deployed and verified. Quiesce old monitors
through the supported service lifecycle; run one current owner per context.
Do not reset tables/checkpoints or discard deferred reports. There is no mixed
old/new cleanup-policy window or compatibility decoder in this plan.

Reverting code can stop further accelerated deletion but cannot restore raw
rows already consumed. The collation/exporter state remains usable by the old
schema-compatible monitor, subject to its old scheduling delays. An operational
rollback must retain all private tables and external files. If the read plan is
also reverted, lost history will remain unavailable; backups/explicit exports
are the recovery option. Never turn raw deletion off by deleting its retry
ledger. No operator data migration or commit is authorized by this request.

The original combined plan's proposed two-table design is rejected: it removed
custody proofs rather than redundant mechanisms. Its zero-retention/default-mode
claims and hard boundedness claim are also rejected. This revision changes
eligibility at existing seams and keeps the recovery state those seams need.

Author fresh-eyes review must check the split's release dependency, all four
ownership modes, checkpoint crash boundaries, summary completeness, and exporter
outbox lifetime. Independent review reads both plans plus source code/specs and
program theory; prefer another agent family. Record every finding's disposition
and verify accepted fixes before implementation. Initial independent verdict: BLOCKED on F2 and companion F1.
Accepted-fix verification: PASS (October 14), scoped to F1/F2/C1 and new
defects introduced by their fixes. No new defects were found.

Author fresh-eyes pass (October 14): retained the existing checkpoint order
rather than adding a new watermark; verified the existing SQL already supports
non-summary exact-reference selection. Made raw-external ownership and the
meaning of the old age setting explicit. No extra recovery table or telemetry
schema change is justified. The private store remains necessary export custody;
losing it after raw deletion can lose telemetry. Different-family review could
not start (Claude OAuth expired; Gemini workspace trust); a fresh same-family
reviewer completed both passes.


### Independent review dispositions (October 14)

| ID | Finding | Disposition |
|---|---|---|
| F2 (P2) | Generic JSONL drain bypasses distinct per-launch report and keep-newest contracts. | Accepted. §3.4 and the exact [MF-5]/[MF-6] delta explicitly retire both, preserve deferred delivery and launch custody, remove the obsolete compactor, and add observable regression cases. This is an intentional loss of historical export detail. |
| F1 (P2, companion) | Terminal projection authority unspecified. | Accepted in read plan §3.1 with task-completion precedence and config/batch/liveness constraints. |
| C1 | Retention ownership ambiguous. | Read plan §3.2 now gives exact policy-owner/clock split; reserved clock stays unchanged here. |


Independent review evidence and reviewed design hashes are recorded in the
read plan §8. Both plans passed accepted-fix verification; plan metadata tests
passed (4 tests), local document links resolved, and `git diff --check` passed.
This is an implementable proposal, not runtime verification, spec approval, or
authorization to execute the future slices. No implementation deviation has
occurred because no implementation was requested.
