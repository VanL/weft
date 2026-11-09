# Bootstrap Skips a Resident Without Identity; Reconciliation Retries Only Transient Failures

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.8]; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.6], [MANAGER.11]
Superseded by: none

Class: 5 (implementation with spec revision). Risky trigger: Manager bootstrap
and launch custody on the durable spine (`Manager -> queues/state log`), so the
hardening checklist applies. Normative text in [MA-1.8] changes. Promotion
strategy B (atomic: spec text and code in one commit).

## 1. Goal

A successor Manager must not be stopped forever by one retained checkpoint
resident that carries no runtime identity. Today such a row makes bootstrap
raise `KeyError`, which `Manager._refresh_scheduler_evidence` swallows at
debug level and retries every `MANAGER_ADMISSION_RECHECK_SECONDS`. Bootstrap
never completes, the public lane is refused (`bootstrap_incomplete`), a queued
request holds the idle clock, and the Manager neither dispatches nor exits,
with nothing logged above debug.

The owner decided the fix on 2026-11-08 (binding, do not exceed):

1. A resident whose identity evidence is missing goes down the existing
   `RuntimeIdentityMismatch` path: skip that one resident, protect its custody
   exactly as that path does today, warn once through
   `_warn_skipped_bootstrap_evidence`; the rest of bootstrap proceeds. One
   policy path, no new mechanism, no pre-validation pass, no read-back.
2. Narrow the catch-all in `_refresh_scheduler_evidence` so a structural error
   can no longer become a silent infinite debug-level retry; only genuinely
   transient failures stay retried. Trace each type first (section 6).
3. The idle-clock rule does not change: pending requests correctly hold the
   Manager. That symptom ends once bootstrap completes.

## 2. Source Documents and Spec Baseline

- `docs/program-theory.md` [WT-1] ("Failures remain visible where they
  happened"), [WT-2a.13] (missing evidence is `unknown`, never proof of death
  or a replay licence), [WT-2a.14] (submission never blocks), [WT-2a.15] (a
  refusal caused by one record holds that record), [WT-4.4] ("No
  over-armoring", "No capability diminution").
- `docs/specifications/03-Manager_Architecture.md` [MA-1.8], the paragraph
  that begins "Because initial task-state publication is best-effort", in
  particular: "A successor dispatches no ordinary work until its bootstrap
  completes" and "A retained row whose recorded runtime identity does not
  match the reading Manager (...) is not that Manager's evidence: bootstrap
  skips it, emits one runtime warning naming the skipped rows, and
  continues."
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5] and
  `docs/specifications/07-System_Invariants.md` [OBS.6]: the pre-launch
  checkpoint persists the exact reservation reference and the validated runtime
  identity before process creation; missing evidence never authorizes replay.
- `docs/specifications/04-SimpleBroker_Integration.md` (runtime context
  bullet): runtime state retained under one context is not adopted by another;
  a Manager skips it at bootstrap with a warning ([MA-1.8]). Unchanged.
- Prior plan, completed and still describing current behaviour:
  [`2026-11-05-manager-wake-idle-clock-and-settlement-repairs-plan.md`](./2026-11-05-manager-wake-idle-clock-and-settlement-repairs-plan.md)
  Task 5 introduced `RuntimeIdentityMismatch`, the bootstrap skip, the
  one-warning record and `_skipped_custody`. Its comprehension answer 5 kept
  "malformed checkpoints" on their old rule; this plan moves one malformed
  shape (absent identity fields) onto the skip, by owner decision.
- Spec baseline: `08c2f87b` (`perf/admission-base`) for all files above.
- Promotion baseline identifier: diff base `08c2f87b`; spec delta and code land
  in the same commit on `perf/fix-bootstrap-resident`.

## 3. Evidence and Causal Premise (verified on base)

- Replay: a copy of the stuck context (from the diagnosing session's
  scratchpad) was bootstrapped by a `Manager` built on base code. Its
  predecessor checkpoint holds resident `1793974122786041856` with only
  `root_tid, submitter_tid, ancestry_tids, scheduling_class, connection_owner,
  host_processes ([]), source_queue, reserved_queue, request_id`. Two
  `_refresh_scheduler_evidence` passes each logged `DEBUG Unable to reconcile
  scheduler evidence ... KeyError: 'context'` and left
  `_admission_bootstrapped = False`.
- Real producers always write the fields: `Manager._checkpoint_admission_launch`
  writes `context` and `hostname` before process creation, and
  `BaseTask._build_tid_state_payload` writes `context, root_tid,
  submitter_tid, ancestry_tids, scheduling_class, connection_owner` together
  whenever a runtime identity is installed. The row was hand-written by a
  reviewer's probe. The fix is owner-directed hardening of the reader, not a
  producer bug.
- RED test (section 8) on base: the new case fails after 15 s / 184 turns with
  `diagnostics={'bootstrapped': False}`; the captured debug log shows 15
  `KeyError: 'context'` tracebacks (one per maintenance pass).

## 4. Current Structure (before this change)

- `Manager._adopt_scheduler_evidence(tid, evidence)` (`weft/core/manager.py`
  ~2616): returns early when `root_tid` is absent/None or `role ==
  "manager"`; otherwise builds `RuntimeIdentity(context=evidence["context"],
  ancestry_tids=tuple(evidence["ancestry_tids"]),
  scheduling_class=evidence["scheduling_class"],
  connection_owner=evidence["connection_owner"], ...)`. A wrong context raises
  `RuntimeIdentityMismatch`; a mismatched connection owner raises it from
  `RuntimeIdentity.__post_init__`. An absent field raises `KeyError`.
- `Manager._bootstrap_scheduler_evidence` (~2924) adopts dead Managers'
  checkpoint residents, then task snapshots not covered by a live peer. Each
  call catches only `RuntimeIdentityMismatch`: it records the row in `skipped`
  and, for checkpoint residents, calls `_protect_skipped_launch_custody`,
  which adds `(reserved_queue, request_id)` to `_skipped_custody`.
  `_maintain_orphan_reservations` treats `_skipped_custody` as protected, so
  the reserved request is never moved back to the source queue. Then
  `_warn_skipped_bootstrap_evidence(skipped)` logs one warning for TIDs not
  already reported (`_bootstrap_skipped`).
- `Manager._refresh_scheduler_evidence` (~2833) runs bootstrap once, then a
  bounded reconciliation batch; its per-resident reread also catches
  `RuntimeIdentityMismatch` and warns. The whole body is wrapped in `except
  (BrokerError, OSError, RuntimeError, ValueError, TypeError, KeyError)` that
  logs at debug; `_admission_bootstrapped = True` is set only in `else`.
- Public dispatch is refused with reason `bootstrap_incomplete` while
  `_admission_bootstrapped` is false. `_manager_owned_work_pending` (~1233)
  counts a pending spawn-inbox row as owned work, which keeps the idle clock
  from running. Retained custody is maintenance, not owned work.

### Comprehension questions (answer before the first edit)

1. *Which component may move a dead Manager's reserved request back to the
   source queue, and what stops it for a skipped resident?* Expected:
   `_maintain_orphan_reservations` (via `_recover_unlaunched_reservations`);
   `_protect_skipped_launch_custody` puts the pair in `_skipped_custody`, which
   it unions into `protected`.
2. *After the fix, what happens to the skipped resident's reservation and to
   its would-be children?* Expected: the reservation stays in the dead
   Manager's reserved queue (held, never replayed, never acknowledged); no
   frontier or ancestry hold is built for the TID, exactly as for a
   foreign-identity row today.
3. *If `ValueError` leaves the except tuple, where does a malformed retained
   row's error go?* Expected: out of `process_once` and `run_until_stopped`;
   `drive_scope` finalizes the task and the Manager process exits with the
   traceback (a detached Manager's stderr file).

## 5. Invariants and Constraints

Must not change:
- Reserved-queue custody ([WT-2a.7], [MANAGER.11]): a skipped resident's
  request is never replayed or acknowledged by the successor.
- A failed broker read or write during reconciliation is logged and changes
  nothing; a successor's failed first checkpoint publication keeps bootstrap
  incomplete and is retried ([MA-1.8], retired admission-evidence plan).
- The idle rule (`_manager_owned_work_pending`) and the `bootstrap_incomplete`
  refusal stay as they are.
- `RuntimeIdentity` validation, `_warn_skipped_bootstrap_evidence`'s
  once-per-TID rule, and `_protect_skipped_launch_custody` stay as they are.
- `root_tid` absent still means "not scheduler evidence" (the early return):
  task snapshots without an installed identity rely on it.

Not allowed: a pre-validation pass over checkpoints; reading a row back; a new
mode, flag or exception type; catching structural errors elsewhere to
compensate; touching `_record_reaped_admission` or `_reconcile_launch_now`
except tuples; changing what the checkpoint writes.

## 6. Except-tuple trace (owner decision 2)

Scope: everything reachable from the `try` body of
`_refresh_scheduler_evidence` on base: broker acquisition,
`latest_task_state_rows`, `_bootstrap_scheduler_evidence` (and its
`_manager_checkpoint_residents`, `_adopt_scheduler_evidence`,
`_recover_scheduler_paths`, `_restore_family_pending`,
`_recover_unlaunched_reservations`, `_publish_scheduler_checkpoint`),
`_maintain_orphan_reservations`, `read_task_state_snapshot`,
`_advance_launch_custody` (with `_snapshot_hands_off_launch`,
`_ack_launch_custody`, `_read_postgres_connection_usage`),
`_retire_scheduler_residents` and `_publish_scheduler_checkpoint`.

| Type | Where it can arise | Transient? | Decision |
| --- | --- | --- | --- |
| `BrokerError` (incl. `DatabaseError`, `OperationalError`; PG wraps `psycopg.Error`) | every broker read and write | yes | keep (not under review) |
| `OSError` | storage I/O | yes | keep (not under review) |
| `RuntimeError` | (a) `_bootstrap_scheduler_evidence` raises it when the successor's first checkpoint publication fails; [MA-1.8] requires that this keep bootstrap incomplete and retry. (b) `_ack_launch_custody` raises "launch acknowledgement not confirmed" when the exact delete is not yet observed; custody must survive to the next pass. (c) SimpleBroker raises `RuntimeError("Failed to get database connection: ...")` after its own connection retries, and "Broker session is closed". Also SimpleBroker misuse guards (fork safety, re-entrant batch), indistinguishable by type. | yes for (a)-(c) | **keep**: legitimately transient; the same type also carries the publish-failure barrier |
| `ValueError` | `RuntimeIdentity` validation of retained values (empty/non-string context, context not matching backend, malformed TIDs, cyclic ancestry, unknown scheduling class); "task scheduling identity changed"; `_admission_host_processes` (non-positive PID, non-numeric create time); `RunnerHandle.from_dict`; SimpleBroker argument validation of retained values (queue names, `exact_timestamp`); `get_connection_usage` only for a non-PostgreSQL queue, which the backend check makes unreachable. `RuntimeIdentityMismatch` (a subclass) is caught where adoption is called. | no: each is a property of a retained row or a programming error and recurs on every pass | **remove** |
| `TypeError` | `_manager_checkpoint_residents` ("invalid Manager resident checkpoint"); `_admission_host_processes` ("must be a list"); `tuple()` of a non-iterable ancestry; SimpleBroker argument type checks | no | **remove** |
| `KeyError` | absent fields of retained evidence: `evidence["context" / "ancestry_tids" / "scheduling_class" / "connection_owner"]` in adoption (the bug, now a `RuntimeIdentityMismatch`), `evidence["request_id"]` when `reserved_queue` is present (`_ack_launch_custody`, `_retire_scheduler_residents`); in-memory lookups are guarded | no | **remove** |

After the change: `except (BrokerError, OSError, RuntimeError)`.

## 7. Observable-Difference Register

| Change | Class | Theory basis |
| --- | --- | --- |
| A retained row that names a root task but lacks `context`, `ancestry_tids`, `scheduling_class` or `connection_owner` is skipped at bootstrap with the one warning; a checkpoint resident's reservation stays protected; bootstrap completes. Before: bootstrap aborted on every pass forever. | A (owner 2026-11-08) | [WT-2a.15] a refusal caused by one record holds that record; [WT-2a.13] missing evidence never authorizes replay |
| A successor whose predecessor left such a row dispatches unrelated public work; with no pending request its idle clock runs and it can idle-exit. Before: leaked Manager, no dispatch. | C (restores [MA-1.8] "dispatches ... once bootstrap completes") | [WT-2a.14], [WT-2a.15] |
| The same row met by the periodic per-resident reread: warned once, that TID continues with no snapshot. Before: the pass aborted. | A (same path as today's mismatch) | [WT-2a.15] |
| The same shape reaching `_record_reaped_admission` or `_reconcile_launch_now` becomes a `RuntimeIdentityMismatch` (a `ValueError`) that their existing handlers debug-log. Before: `KeyError`, which neither handler catches, escaped. | N on real paths (task snapshots always write all identity fields with `root_tid`) | [WT-4.4] no over-armoring: no change to those handlers |
| `_refresh_scheduler_evidence` no longer catches `ValueError`, `TypeError`, `KeyError`. A malformed retained row (other than missing identity) now stops the Manager with a traceback in its stderr log, at bootstrap and in later passes. Before: debug-level retry every `MANAGER_ADMISSION_RECHECK_SECONDS`; at bootstrap a silent leaked Manager, after bootstrap a silently aborted reconciliation pass while dispatch went on. A successor meeting the same row stops the same way until the row is removed. | A (owner 2026-11-08) | [WT-1] failures remain visible where they happened; [WT-4.4] no over-armoring (real paths never write such rows). Tension with [WT-2a.15] recorded as an open question |
| Warning text: "retained row(s) that name another runtime identity ... Retained state written under another context is not adopted" becomes "retained row(s) that do not carry this Manager's runtime identity ... Retained state from another context, or with no recorded identity, is not adopted." The reason now says which field is missing. | N (log text; still one warning naming the rows) | [WT-1] |
| Broker, OS and runtime failures (including the failed first checkpoint publication) are still retried with a debug log. | N | [MA-1.8] |

No D rows.

## 8. Proposed Spec Delta

| Spec file | Strategy | Sections touched |
| --- | --- | --- |
| docs/specifications/03-Manager_Architecture.md | B (atomic) | [MA-1.8], the sentence beginning "A retained row whose recorded runtime identity"; plan backlink list |

Replace the sentence "A retained row whose recorded runtime identity does not
match the reading Manager (it names another context, or its connection owner
does not match its runtime identity) is not that Manager's evidence: bootstrap
skips it, emits one runtime warning naming the skipped rows, and continues."
with:

> A retained row whose recorded runtime identity does not match the reading
> Manager (it names another context, its connection owner does not match its
> runtime identity, or it names a root task but records no context, ancestry,
> scheduling class or connection owner) is not that Manager's evidence:
> bootstrap skips it, emits one runtime warning naming the skipped rows, and
> continues. A skipped checkpoint resident's reserved request stays held; it is
> neither replayed nor acknowledged. Reconciliation retries a failed broker
> read or write, including the successor's failed first checkpoint
> publication, at the next maintenance pass; any other error from retained
> evidence is a defect in that evidence, is not retried, and stops the Manager
> with that error.

Clause audit of the replaced sentence: both existing cases (another context,
owner mismatch), "not that Manager's evidence", "skips it", "one runtime
warning naming the skipped rows" and "continues" are kept verbatim. The
custody sentence states existing behaviour (`_skipped_custody`, 2026-11-05
plan round-2 F1). The retry sentence is consistent with the earlier sentences
of the same paragraph ("A failed read ... is logged and changes nothing" and
"A successor dispatches no ordinary work until its bootstrap completes").

Backlink entry, added at the top of the 03 plan list:

> - [Bootstrap Skips a Resident Without Identity](../plans/2026-11-08-bootstrap-resident-without-identity-plan.md) skips a retained row that records no runtime identity at bootstrap, as it skips a foreign one, and retries only transient failures during reconciliation ([MA-1.8]).

## 9. Tasks

1. RED test.
   - File: `tests/core/test_admission_recovery.py`.
   - Extend the table of
     `test_successor_skips_mismatched_identity_and_preserves_reservation`
     from `bad_snapshot: bool` to `corruption` in `checkpoint_owner`,
     `snapshot_owner`, `checkpoint_identity_missing`. The new case rewrites
     the predecessor's checkpoint resident to exactly the probe's shape (the
     eight lineage and custody keys plus `host_processes: []`; no `context`,
     `hostname`, `launched_ns`). Same seam as the existing cases: real
     `recovery_manager`, real broker, a real successor `Manager` driven by
     `drive_until(step=process_once)`.
   - Assertions (all cases): the unrelated public request leaves
     `weft.spawn.requests`; bootstrap is complete and stays complete across two
     more maintenance passes; exactly one "Manager bootstrap ignored" warning,
     naming the skipped TID; the original reserved request is still in the
     predecessor's reserved queue and not in the source queue. Add
     `diagnostics` to `drive_until` so a failure shows the bootstrap flag.
   - Done when: on base only the new case fails, by timeout with
     `bootstrapped: False`, and the debug log shows `KeyError: 'context'`.
   - Second test, same file, sharing the setup through two small helpers
     (`_dead_predecessor_checkpoint`, `_successor_of`) that the first test
     also uses: `test_successor_stops_on_malformed_retained_resident`, one
     case per type removed from the except tuple: `scheduling_class:
     "bogus"` (`ValueError` from `RuntimeIdentity`), `host_processes:
     "bogus"` (`TypeError` from `_admission_host_processes`), and
     `host_processes: []` with `request_id` dropped (`KeyError` in
     `_retire_scheduler_residents`). It calls `_refresh_scheduler_evidence()`
     directly (the reconciliation owner; existing tests use the same seam)
     and asserts the error escapes and bootstrap stays incomplete. Calling
     `process_once` instead is not sensitive for the `KeyError` case: on
     base that shape already escapes the turn from
     `_dispatch_causal_refusals` (`KeyError: 'request_id'`).
2. Fix (`weft/core/manager.py`).
   - New `Manager._scheduler_evidence_identity(tid, evidence)` builds the
     `RuntimeIdentity` exactly as `_adopt_scheduler_evidence` did and turns a
     `KeyError` into `RuntimeIdentityMismatch(f"scheduler evidence records
     no {missing.args[0]}")`. Nothing inside that call raises `KeyError`
     except the four field reads. `_adopt_scheduler_evidence` calls it at the
     point it read the fields. (A helper rather than an inline `try`, because
     the inline form raised `_adopt_scheduler_evidence` to C901 complexity 11;
     no suppression was added.)
   - In `_refresh_scheduler_evidence`, change the except tuple to
     `(BrokerError, OSError, RuntimeError)`.
   - In `_warn_skipped_bootstrap_evidence`, update the docstring and the
     warning text (section 7).
   - Stop if: any existing owning test relies on a swallowed `ValueError`,
     `TypeError` or `KeyError` from a real (non-hand-written) path; that
     would falsify the trace in section 6.
3. Spec delta: apply section 8 to `03-Manager_Architecture.md`.
4. Sensitivity checks: revert only the identity-read change, rerun the new
   skip case, record the failure, restore exactly; then revert only the
   except tuple, rerun the malformed-row test, record, restore exactly
   (file checksum equal to before).
5. Plan index row in `docs/plans/README.md` (`draft`), file count.

Out of scope: `_record_reaped_admission` and `_reconcile_launch_now` except
tuples; a checkpoint resident with no `root_tid` (section 12); malformed but
present identity values; published refusal reasons; Track B checkpoint field
projection.

## 10. Testing Plan and Gates

- Do not mock the broker, the Manager, reservation moves or the checkpoint
  queue; the test writes the corrupt row through a real queue and drives a
  real successor.
- Owning files, SQLite (`-n 4`):
  `./.venv/bin/python -m pytest tests/core/test_admission_recovery.py
  tests/core/test_manager_family_maintenance.py
  tests/core/test_family_source_reconciliation.py
  tests/core/test_family_dispatch_scaling.py tests/core/test_runtime_identity.py
  tests/system/test_relocated_context.py tests/system/test_family_recovery.py
  -n 4 -q`
- The same targets via `./bin/pytest-pg <targets>`.
- `./.venv/bin/ruff check .`, `./.venv/bin/ruff format --check` on touched
  files, and the repo mypy command from `CLAUDE.md` §5.
- Not run here: `tests/system/test_admission_stress.py`, the full suite.
- Runtime signal: a Manager log line "Manager bootstrap ignored 1 retained
  row(s) that do not carry this Manager's runtime identity (for example
  <tid>: scheduler evidence records no context)" and dispatch resuming.

## 11. Rollback and Composition

Revert the single commit. The change writes no new data and changes no queue
or payload shape, so old and new Managers read the same rows; rollback only
restores the debug-level retry. No rollout order.

Composition with sibling branches off `08c2f87b`:
- `perf/manager-checkpoints` (Track B) projects checkpoint residents onto
  `MANAGER_CHECKPOINT_RESIDENT_FIELDS`, which keeps `context`,
  `ancestry_tids`, `scheduling_class`, `connection_owner` and `hostname`, so
  real checkpoints still carry every field this plan reads; adoption reads
  are unchanged there. Its `_bootstrap_scheduler_evidence` still raises
  `RuntimeError` on a failed first publication, which this plan keeps
  retried. The fix composes. Textual merge: Track B (and Track H,
  `perf/manager-dispatch`) replace the two lines just above the
  `_refresh_scheduler_evidence` except clause with
  `self._commit_scheduler_checkpoint()`; a merge will show adjacent-hunk
  conflict there, resolved by keeping both edits.
- Track H also wraps `_record_reaped_admission`'s read in the Manager's own
  connection; its except tuple is unchanged and out of scope here.

## 12. Open Questions for the Owner

- Malformed-but-present identity values and other per-record structural
  errors (malformed `host_processes`, "task scheduling identity changed")
  now stop the Manager instead of being retried. [WT-2a.15] would scope such a
  record to itself (skip it); this plan does not, because the decision limits
  the skip to missing identity. Decide whether those should also take the
  skip path.
- A checkpoint resident with no `root_tid` is silently not adopted (the early
  return that task snapshots without identity need), and its reservation is
  not added to `_skipped_custody`, so `_maintain_orphan_reservations` may move
  it back to the source queue when no task snapshot exists. Only a
  hand-written row has this shape. Unchanged here.
- Observation, not changed: on base a checkpoint resident with
  `reserved_queue` but no `request_id` already escapes the reactor turn from
  `_dispatch_causal_refusals` (`KeyError: 'request_id'`), outside the except
  tuple this plan narrows. Only a hand-written row has this shape.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Review and execution record

Comprehension answers (implementer, before the first edit): (1)
`_maintain_orphan_reservations`, stopped by `_skipped_custody` through
`_protect_skipped_launch_custody`; (2) the reservation stays held in the dead
Manager's reserved queue, no frontier or ancestry hold for the TID; (3) out of
`process_once` and `run_until_stopped`; `drive_scope` finalizes and the
Manager exits with the traceback.

Execution (spec baseline and diff base `08c2f87b`):
- RED: `test_successor_skips_mismatched_identity_and_preserves_reservation[checkpoint_identity_missing]`
  failed on base after 15 s / 184 turns, `diagnostics={'bootstrapped':
  False}`, with 15 debug `KeyError: 'context'` tracebacks; the two existing
  cases passed.
- GREEN: all three cases pass; the warning reads "... (for example <tid>:
  scheduler evidence records no context) ...".
- Replay of a copy of the stuck context with the fix: both passes report
  `bootstrapped=True`; the probe resident is skipped and its pair
  `('T1793974121842880512.reserved', 1793974122786041856)` is in
  `_skipped_custody`. On base both passes reported `bootstrapped=False`.
- Sensitivity 1 (identity read reverted, narrowed tuple kept): the new skip
  case fails with `KeyError: 'context'` escaping the turn; restored, file
  checksum identical. Repeated on the final helper form with the same
  result.
- Sensitivity 2 (except tuple reverted): all three
  `test_successor_stops_on_malformed_retained_resident` cases fail with
  "DID NOT RAISE"; restored, checksum identical.
- SQLite, `-n 4`: `tests/core/test_admission_recovery.py
  tests/core/test_manager_family_maintenance.py
  tests/core/test_family_source_reconciliation.py
  tests/core/test_family_dispatch_scaling.py tests/core/test_runtime_identity.py
  tests/system/test_relocated_context.py tests/system/test_family_recovery.py
  tests/core/test_manager.py tests/specs/test_plan_metadata.py`: 425 passed,
  4 skipped (PostgreSQL-only).
- PostgreSQL, `./bin/pytest-pg` on the same core and system files (without
  the SQLite-only relocated-context and plan-metadata files) plus
  `tests/system/test_measured_admission_pg.py`: 420 passed, 12 skipped.
- `ruff check .`, `ruff format --check` on touched Python files,
  `bin/check-doc-paths`, and the repo mypy command: clean.

Fresh-eyes self-review (author, after drafting and again after
implementation): every symbol and path named here exists; the replaced spec
sentence was quoted exactly and replaced once; the custody clause describes
existing code; the `RuntimeError` row keeps the bootstrap publication barrier
and its test (`test_failed_bootstrap_checkpoint_keeps_restored_work_undispatched`)
stays green. Independent cross-family review: to be recorded here by the
coordinator (this agent was told not to call external reviewers), which is a
deviation from Class 4/5 review-before-implementation.

### Cross-family review (coordinator)

Tier 1, Codex (`codex exec -s read-only`, reasoning effort high), 2026-11-08,
scoped-change review of plan and implementation at `671f3246` against
`08c2f87b`. Verdict: no blocker, no in-scope findings. The reviewer confirmed
the causal premise on base, that missing identity now enters the existing
mismatch path (which protects reservation custody before orphan recovery), and
that keeping `RuntimeError` preserves retries for failed bootstrap publication
and unconfirmed acknowledgement. Owner questions left open: whether other
malformed retained rows should also be skipped per [WT-2a.15], and the
no-`root_tid` row that stays ignored.
