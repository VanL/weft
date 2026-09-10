# Guard and Custody Simplification Plan

Status: draft
Source specs: docs/specifications/07-System_Invariants.md [QUEUE.6], [OBS.1], [OBS.6], [OBS.6a], [LIVENESS.R3], [LIVENESS.R5], [LIVENESS.R6]; docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-3], [MF-3.1], [MF-5]; docs/specifications/03-Manager_Architecture.md [MA-1], [MA-3]; docs/specifications/01-Core_Components.md [CC-2.4.1], [CC-2.5], [CC-3.2]; docs/specifications/02-TaskSpec.md [TS-1], [TS-1.1], [TS-1.3]; docs/specifications/14-Python_API_Surfaces.md [PY-2]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4a]; docs/specifications/00-Quick_Reference.md
Superseded by: none

Class: 5 — program ledger for a set of changes that include normative spec
edits. This file carries **no implementation tasks**. It is the audit
inventory, the decision record, and the index of the focused plans that
implement the work. Each child plan carries its own class, baseline,
deltas, tasks, and review loop.

## 1. Goal

A 2026-08-31 five-subsystem complexity audit found that the failure class
fixed by the liveness custody split — multiple deleters of one state
queue, filters that strand state, guards against states the invariants
already preclude, and migrations that never retired their predecessors —
is present elsewhere in the system. Two independent adversarial reviews of
the first draft of this plan, followed by code-level verification of every
contested claim, corrected several of the audit's conclusions (some
proposed deletions were spec-mandated behavior; one "ordering bug" was
predicate-gated and not a bug) and surfaced two production defects nobody
had listed. The revised direction, in Van's words for the custody rule as
refined by review: **writers may exact-delete rows they own; one
cross-owner reaper owns stale-row policy; readers filter and never
delete** — plus: no policy value the system cannot honor, and no rollback
switch that leaves a component unable to do its job.

## 2. Child Plans (implementation lives here, in this order)

| # | Plan | Class | Why this order |
|---|------|-------|----------------|
| 1 | [2026-08-31-runtime-identity-custody-plan.md](./2026-08-31-runtime-identity-custody-plan.md) | 5 | Two reproduced production defects with one root cause (unreleased worker identity); adds the [CC-3.2] post-reap identity rule; prerequisite for meaningful endpoint tests in plan 4 |
| 2 | [2026-08-31-reserved-disposition-and-requeue-removal-plan.md](./2026-08-31-reserved-disposition-and-requeue-removal-plan.md) | 5 | Backstop drains can delete rows an invariant says to preserve; `requeue` is a promise the system cannot keep |
| 3 | [2026-08-31-monitor-and-task-correctness-fixes-plan.md](./2026-08-31-monitor-and-task-correctness-fixes-plan.md) | 4 | Independent fixes against existing spec text; execution and cleanup paths require hardening; no spec change |
| 4 | [2026-08-31-registry-custody-contracts-plan.md](./2026-08-31-registry-custody-contracts-plan.md) | 5 | Services/endpoints custody and short-TID resolution need spec text changed first |
| 5 | [2026-08-31-dead-generation-retirement-plan.md](./2026-08-31-dead-generation-retirement-plan.md) | 5 | Mechanical deletions; Class 5 only for one Quick Reference correction |
| 6 | [2026-08-31-collation-store-toggle-removal-plan.md](./2026-08-31-collation-store-toggle-removal-plan.md) | 5 | Removes an opt-out mode in which the monitor cannot do its job, and the second selection engine kept alive for it |
| 7 | [2026-08-31-short-tid-derivation-plan.md](./2026-08-31-short-tid-derivation-plan.md) | 5 | Folded short-TID derivation, split out of plan 4 so its promotion is independently reviewable; depends on plan 4 task 7 |

Plans 1–3 and 5 are independent of each other. Plan 7 depends on plan 4's helper. Plan 4 depends on plan 1
(endpoint characterization tests are meaningless while persistent tasks
lose their claims after the first work item). Plan 6 is independent.

### 2026-09-07 plan-revision scope

Class 5: revise the proposed runtime contracts and their implementation
instructions after the current-state review; no spec promotion or code
implementation in this revision. Baseline: specs and code at `bcea628e`,
with the reviewed draft texts captured before editing. Existing owner
decisions in §4 remain unchanged. These child plans remain the execution
documents; this section records only the revision work:

1. Correct the four runtime interactions: self-control after worker reap,
   repeated reserved disposition after successful work, postponed discovery,
   and service TTL units. Synchronize claims, invariants, deltas, and tests.
2. Correct logging instructions, collision estimates, rollback wording,
   and the adjacent
   [CLI broker-session plan](./2026-09-02-cli-process-broker-session-plan.md)'s
   failure-release test and normalized metadata.
3. Review the revisions independently against the accepted findings, run
   `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q`,
   and inspect the revision diff for conflicting instructions. Runtime
   regression tests prescribed by the plans are future implementation
   gates, not tests claimed to pass in this documentation revision.

The governing spec sections are those in each child's `Source specs` and
`Proposed Spec Delta`; the broker-session plan adds [SB-0.4]/[PY-1]. No
runtime capability, approved removal, public API decision, or promotion
strategy is selected by correcting these drafts.

## 3. Findings Inventory and Dispositions

Every row was verified against code and spec text on 2026-08-31 (HEAD
`bcea628e`). "Provenance" answers: is the behavior merely present in code,
held in place only by tests, or mandated by a spec section — and if the
last, does the spec change.

| Finding | Provenance | Disposition | Plan |
|---------|-----------|-------------|------|
| `_managed_pids`/`_runtime_handle` never released; OS-signal termination signals historical raw PIDs with no `(pid, create_time)` check; merge can re-observe a managed PID missing from a replacement handle (identities already in the input are preserved) | Code out of conformance with [LIVENESS.R5], [CC-3.2], [MA-1] | Fix code; add the [CC-3.2] post-reap/same-instance rule and keep published task identity out of active worker control (Class 5) | 1 |
| Persistent named task loses its endpoint claim after its first work item (published handle becomes the dead worker's identity; reproduced with real broker) — same root cause; also makes the LivenessMonitor see an idle persistent task as `stale` | Code out of conformance with [CC-3.2] | Fix code; covered by the same [CC-3.2] delta | 1 |
| Reserved-queue backstop drains (`_ensure_reserved_empty` triad at 10+ task sites; Manager `_ensure_reserved_queue_empty`) delete rows a failed policy application or failed success-ack left behind; deferred control can repeat disposition after successful work | Code; violates [QUEUE.6]/[MF-2] | Delete drains and prevent repeated disposition on the successful-outcome deferred-control path; extend [MF-2] text | 2 |
| Task-level `requeue` moves a row to `T{tid}.inbox` after the only consumer of that queue has gone terminal; no same-TID rerun facility exists; its two tests assert only the row move | Code + tests; [QUEUE.6] defines only the move | Remove `requeue` from task specs (Van, 2026-08-31); Manager spawn-return keeps a hardcoded move | 2 |
| Interactive session-start failure writes no terminal ctrl_out envelope; interactive finalize uses a bare `write` (no bounded retry); STOP/KILL ack precedes `_interactive_shutdown` | Code; [MF-3] requires post-unwind ack | Fix code; spec unchanged | 3 |
| `retire_completed_collation_families` called from two lanes while a comment claims one owner; reserved-slice call sits behind `if not errors` | Code; retirement is predicate-gated so no stranding (the "ordering bug" claim was wrong) | Keep store-cycle call; delete slice call and comment | 3 |
| Queue-discovery deadline reset to 0.0 every store cycle; after removing that reset, the result handler would postpone it even when discovery was skipped | Code; [OBS.13.12] requires bounded work and progress; slice-only tests miss the result-handler interaction | Remove resets and preserve the deadline on skipped discovery; test bounded scans and eventual discovery under catch-up | 3 |
| `_closed_activity_waiter_ids` `id()`-keyed append-only set (id reuse → leaked resource, reproduced) | Code | Per-object closed state | 3 |
| SimpleBroker return-shape guards that would strand a moved reserved row | Code; violates the layer rule | Delete | 3 |
| Negative-sentinel branches with contradictory meanings on positive `Final` constants | Code; held by tests that monkeypatch `-1.0` | Replace tests, delete branches | 3 |
| `weft.state.services` has four deleters across three processes; `weft status` performs writes and blocking probes | **Spec-mandated** ([MA-1.4]: callers "prune dead or expired active records") | Change [MA-1.4] and [MA-1]; readers filter; pruner deletes; Manager keeps own-row lifecycle **and the supersede re-check** (load-bearing for `--replace`, review B1) | 4 |
| `weft.state.endpoints` reader-side delete with no age gate; `register_endpoint_name` scan-write-scan-delete | **Spec-mandated** ([MF-3.1]: readers "opportunistically prune stale claims") | Change [MF-3.1] and [CC-2.4.1]; one-shot registration with captured `write()` id; readers filter; pruner deletes | 4 |
| Short-TID collision resolved first-wins in one reducer and last-wins in another (reproduced) | Code; **unspecified** by [CLI-1.2.3]/[OBS.5] | Add spec rule: ambiguity error (silent newest-wins could target the wrong task for `kill`) | 4 |
| Eight hand-rolled "latest mapping row" folds; two private copies of handle-liveness helpers | Code; [OBS.6] specifies newest-row-per-TID | Consolidate on the `endpoints.py` fold (expose `(message_id, payload)`); repoint to `helpers` | 4 |
| Legacy tuple-returning commands generation (~600+ lines, zero production callers); client/CLI forked bodies; dead validate module; dead helpers suite | Code; held by tests | Delete per module; repoint semantic assertions | 5 |
| `stop_many`/`kill_many` empty-selection success and private-seam routing | **Spec-mandated** ([PY-2]: "An empty selection is a successful zero-count outcome") | **Keep behavior**; at most give `_task_control_result` a public name | 5 |
| Dead-TID coalesce/delete chain (zero callers), test-only reserved scan engine, dead store/SQL methods, always-zero result fields | Code; [OBS.13] forbids dead-TID raw-log coalescing anyway | Delete | 5 |
| Dead constants; `RUNNER_DIAGNOSTICS_FIELD` inverted SSOT; two identical terminal frozensets + inline literals; `yield_strategy`; `DEFAULT_FUNCTION_TARGET` | Code; pinned by `test_constants.py` | Delete / repoint / document | 5 |
| `WEFT_MANAGER_CTRL_IN/OUT_QUEUE` name queues the runtime never uses; Quick Reference and CLAUDE.md document them | Code (zero consumers, verified) + stale spec text | Delete constants; correct Quick Reference (strategy D) | 5 |
| `TaskSpec._validate_strict_requirements` ~70 lines, one live check | Code; [TS-1] templates may carry empty IO, so the live check must stay at the BaseTask boundary | Shrink to the one check at `BaseTask.__init__` | 5 |
| Plugin `__init__` re-validation (drifted error message), capability re-checks, registration globals, strict `handle.runner` probe guards | Code; [TS-1.3] names `validate_runner_capabilities` as the gate | Consolidate on the microsandbox `parse_options` shape; delete re-checks | 5 |
| Preflight validator called twice | Code; Protocol does not promise superset | **Leave** (no cost; collapsing is a public contract change) | — |
| Per-open full-table Monitor-store verification; fail-closed on one bad row | **Spec-mandated** ([SB-0.4a]: "no tolerant current reader") | **Dropped** — spec is deliberate; no measured cost | — |
| Retention prune re-derives terminal-ness and can bypass store proofs | **Spec-mandated** separate operator authority ([MF-5]/[CLI-6]); ordinary apply cannot delete inbox/reserved; `--force` is the explicit override | **Dropped** — false positive | — |
| macOS plugin re-implements `inspect_host_process` | Stale — already uses the canonical probe (committed) | **Dropped** | — |
| Window-scan task-log engine + `WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED` opt-out | **Spec-mentioned** ([MF-5] disable sentence; Quick Reference env row). Verified: with the store disabled **no task-local runtime cleanup runs at all** (`runtime_cleanup_ready` is only ever set by the store cycle); no behavioral test covers the mode; `report_only` already is the spec-named non-destructive override | Remove toggle and engine; store always on | 6 |
| Dynamic MultiQueueWatcher topology machinery (`add_queue`/`remove_queue` on a running watcher; no in-repo production caller) | **Spec-mandated** ([QUEUE.8]); used by other projects that embed `MultiQueueWatcher` | **Keep** (Van, 2026-08-31). In-repo caller count is not evidence of deadness for this surface | — |
| Four task-log terminal-ness folds | Code; they answer different questions and partly share extraction | **Leave**; share extraction functions only | — |

## 4. Decisions Recorded

- 2026-08-31 (Van): remove `requeue` from task-level reserved policies.
- 2026-08-31 (Van): remove the collation-store opt-out and the window-scan
  engine; the store is the monitor's collation-and-deletion mechanism.
- 2026-08-31 (Van): short-TID collisions raise a loud ambiguity error,
  **and** the short form is re-derived so the 12 counter bits no longer
  waste ten bits of the ten-digit space. Corrected formula after the Codex
  review (SimpleBroker clears the low 12 bits of `time.time_ns()`; the
  physical part is a 4,096 ns grain, not microseconds):
  `((tid >> 12) + counter × ⌊10¹⁰/4096⌋) mod 10¹⁰`, zero-padded — all
  4,096 counters distinct within a grain. Under independent uniform
  counter-zero residues, the space grows 1,024× and the population at a
  fixed birthday-collision probability grows 32×; these are not measured
  collision rates for correlated task schedules. Split into
  its own plan (7) after the Codex second review; plan 4 keeps the error
  and introduces the helper with today's formula.
- 2026-08-31 (Van): keep `MultiQueueWatcher.yield_strategy` (embedders).
- 2026-08-31 (Codex round, author-applied): plan 1 reclassified Class 5 —
  [CC-3.2] gets an exact post-reap identity rule; the callback design was
  dropped (it changed the `TaskRunnerBackend` Protocol).
- 2026-08-31 (Codex round, author-applied): plan 5 keeps the capability
  re-checks inside `RunnerPlugin.create_runner` — it is a public surface
  with no normative prior-validation precondition; and the legacy
  status-JSON projection test is a firing [CLI-1.2.1] assertion, so the
  projection moves into the CLI adapter instead of the test being deleted.
- **Open owner decision:** guard same-TID relaunch via `Client.submit`
  with a resolved spec's `tid` (reachable today; `_build_child_spec` has no
  terminal-evidence guard). Plan 2 no longer promotes an unenforced
  prohibition; a guard is a separate Class-5 change.
- 2026-08-31 (Van): keep `MultiQueueWatcher.add_queue`/`remove_queue` and
  [QUEUE.8] — the watcher is embedded by other projects that use dynamic
  topology; zero in-repo callers is not deadness evidence for it.
- 2026-08-31 (Van): Manager REQUEUE is kept in full — stop/yield return
  and the policy-driven restore of a failed child launch. The
  `ReservedPolicy` enum keeps `REQUEUE`; removal is scoped to task-level
  application and the public task-spec boundary (plan 2).
- 2026-09-04 (Van): **acceptance rule for the whole program — no
  observable difference in capability, or at the very least no
  diminution, may result from these changes.** Owner-decided removals
  (`requeue` task policy, the store opt-out, the short-form derivation)
  are the only intended differences. Every other difference is listed in
  §4a with its class; anything classed as a diminution is fixed in the
  owning plan, not accepted.
- Custody rule wording adopted from review: writers exact-delete rows they
  own; one cross-owner reaper owns stale-row policy; readers filter.
- 2026-09-04 (Van): retirement-rule reconciliation adopted — every record
  retirable under today's code must keep a named rule after the plans.
  Plan 4 amended: the pruner's manager-row predicate is the boolean of
  `manager_registry_record_is_stale` (both branches, so
  `external-supervisor` rows still retire); managed-service rows are
  deleted by the pruner alone through the reader TTL, and the manager's
  per-convergence deletion is removed outright instead of gaining an
  owner-local id store.
- 2026-09-04 (Van): the runtime pruner deletes an `unknown`-owner manager
  row once it is past all timeouts (`min_age` and the external-supervisor
  window) — no probe at prune time; the external-supervisor age→stale
  conversion in `_manager_record_stale_status` goes as contrary to [MA-1].
- 2026-09-04 (Van): malformed (schema-tagged but invalid) registry rows
  are pruned, and each deletion is logged as an error when logging is on
  ([MF-5]/[OBS.13.6] deltas in plan 4, logged through the `weft.helpers`
  logging facade). Rows with no recognized schema tag stay preserved.
- 2026-09-04 (Van): plan 5 keeps the `weft.helpers` logging facade
  (`send_log`, `debug_print`, `log_*`, `is_logging_enabled`,
  `is_debug_enabled`, `_config`, `reload_config`). The facade implements
  the documented `WEFT_LOGGING_ENABLED`/`WEFT_DEBUG` switches, which
  would otherwise have no reader inside Weft (`weft.helpers` is private
  per [PY-1]; the keep is owner-decided, not a public-surface claim).
- Custody is proven with behavioral tests, not a module-allowlist AST test
  (allowlisting `manager.py`/`base.py` would mask exactly the deletes such a
  test exists to catch).

## 4a. Observable-difference register (2026-09-04, verified in code)

Classes: **A** owner-decided removal; **N** no observable difference in
any shipping configuration; **C** observable difference with no loss of
capability (diagnostic shape, timeliness of physical cleanup, a
correctness fix); **D** diminution — must be fixed in the owning plan.

| Plan | Difference | Class | Evidence / disposition |
|------|------------|-------|------------------------|
| 2 | task-level `requeue` rejected | A | Van 2026-08-31 |
| 2 | reserved residue after a *failed* success-path ack is retired by the reserved slice's name-scan fallback after retention age instead of by an immediate drain | C | `select_runtime_reserved_cleanup_candidates` selects every `T*.reserved` queue after cleanup proof + age; automatic; failure path only |
| 6 | `WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED` removed; window-scan engine deleted | A | Van 2026-08-31 |
| 6 | engine's claimed task-log row delete gone | N | ran only with the store opted out; store default on since it landed; vacuum already owns claimed rows in the default config |
| 6 | PONG: `skipped_owner` stats rows and constant-True `collation_store_enabled` fields disappear | C | diagnostics shape only; no spec names them |
| 6 | task-local cleanup now runs where it never did (store-off mode) | C | strictly more cleanup |
| 4 | readers no longer delete registry rows; stale rows persist until maintenance prune (hourly, default on) or `weft system prune` | C | readers filter identically; rows visible only via queue peek. Non-default `WEFT_TASK_MONITOR_MAINTENANCE=0`: operator prune is the only deleter — stated in plan 4 |
| 4 | per-read keyed PING for `unknown` crashed-manager rows | **D → fixed** | would have cost 0.5 s per row per read for up to ~2 h; plan 4 now omits `unknown` rows older than the 300 s window without probing (10 missed 30 s heartbeats), preserving today's one-probe cost |
| 4 | endpoint rows of stale owners retired by the pruner after `min_age` instead of by the next reader | C | filtered identically; strictly less exposure than today's ungated reader delete |
| 4 | `stop_manager --force` newest-row PID semantics; no false success without a controllable PID | C | correctness fix |
| 4 | short-TID collision is an error instead of silent first-wins | C | correctness fix; `kill` on the wrong task is the capability being protected |
| 4 | malformed schema-tagged service rows deleted by the pruner (≤ ~2 h) instead of the next manager heartbeat, with an ERROR log when logging is on | C | timeliness; new diagnostic |
| 4 | managed-service rows deleted by the pruner after `min_age` instead of the manager's 300 s TTL sweep | C | readers ignore them after 300 s either way |
| 4 | `_register_manager` replay unchanged | N | optimization dropped |
| 5 | PONG extended block loses always-zero dead-TID keys; legacy `cmd_*` wrappers, `_legacy_cmd_status`/`_result`, dead constants removed; tidy/dump raise `CommandExecutionError` | C | no CLI shape change; exception type is the spec-mandated seam translation; release-note |
| 5 | status-JSON broker ids stay strings | N | projection moved into the CLI adapter after review |
| 5 | logging facade kept | N | Van 2026-09-04 |
| 3 | one terminal ctrl_out envelope for interactive tasks; acks after unwind; cadence fixed without starving discovery; waiter ledger; shape guards and negative sentinels removed | C | correctness fixes; interactive ctrl_out ordering preserved |
| 1 | worker PIDs released; persistent tasks keep endpoint claims after the first item; `weft task status` shows the session identity, not the last worker's | C | today's behaviour is the defect (claims lost, stale PIDs signalled) |
| 7 | short forms change under the folded derivation, including counter-zero TIDs; default pipeline names change | A | Van 2026-08-31; old-format mapping rows remain resolvable from full TIDs (plan 7 red test); copied old short strings are not guaranteed aliases |
| 6 (landed) | interactive exit under a lingering child draws an extra KILL pair and returns 1–2 s later (client ack wait 1.0 s vs stop grace 2.0 s) | C, owner confirm | Round 9 finding 5 |
| 4 (landed) | foreground serve preserves an unconfirmed external record instead of writing `superseded`; missing-handle rows reduce to `unknown` and persist through both windows | C, owner confirm | Round 9 findings 4, 6 |
| 1 (landed) | a delivered result whose worker outlives kill+0.2 s becomes `work_failed` | **D, decide** | Round 9 finding 1 |

## 5. Lessons to record in `docs/lessons.md` when the child plans land

1. Three reviewers (the author included) proposed deleting spec-mandated
   behavior. The standing gate for any deletion finding is four questions:
   reachable in production? held only by tests? spec-mandated? if so, should
   the spec change?
2. `requeue` survived because its tests asserted the mechanism (a row move)
   rather than the promise (re-execution). A policy value whose tests never
   assert the outcome its name implies is the signal for a policy the system
   cannot honor.
3. A rollback switch kept after its rollout completes becomes a second
   implementation with weaker rules; the store toggle also silently disabled
   three unrelated cleanup policies and nothing tested it.

## Deviation Log

Not applicable — this ledger implements nothing. Deviations are logged in
the child plans.

## Spec Baseline

- `bcea628e` — all Source specs above, at ledger revision time
  (2026-08-31). The liveness custody split landed in `a27e7dc7`.

## Review Record (append-only)

**2026-09-07 — current-state review and plan corrections.** Four material
interactions were reproduced at `bcea628e`: publishing self as the active
runtime made task-internal stop target self; deferred STOP/CLEAR deleted a
row after a failed success acknowledgement even with drains removed;
skipped discovery advanced its deadline under repeated catch-up; passing
seconds to `ttl_ns` expired a 60-second-old active service row. The owning
plans now specify the corresponding fixes and regression cases. Smaller
corrections cover logging keyword support and the retained logging gate,
PID-merge characterization, collision-probability assumptions, and the
collation plan's irreversible raw-row deletion on forced store adoption.
The adjacent CLI broker-session plan now tests failure after broker use,
requires an omitted-cleanup mutation with armed references retained to
prove its release oracle without GC masking, and has
normalized draft metadata. The §4a plan numbers for correctness (3) and
collation-toggle removal (6) are reconciled with the child index.

Independent review of this revision was scoped to these corrections and
new defects they introduce; prior owner decisions remained closed. Three
reviewers used separate agent contexts in the available same model family;
each reviewed another author's changes against the pre-edit draft and
the owning code. This was not a new full review of every earlier decision.

| Revised plans | Independent disposition |
|---------------|-------------------------|
| 1 and 2: runtime identity and reserved disposition | PASS: mapping fallback leaves no active self-control handle; the successful-outcome flag gates only reserved policy and preserves deferred terminal/control work. |
| 3, 5, and 6: correctness, dead generations, collation toggle | PASS: existing slice/result fields distinguish skipped discovery from a completed chain and retain pending/error retries; the live logging gate remains; rollback wording names irreversible raw deletion. |
| 4 and 7, CLI broker session, and this ledger | PASS: TTL units, actual per-ID deletion logging, collision assumptions, isolated failure test, and ledger reconciliation agree with code. Follow-up PASS covers old mapping rows versus copied short strings and the release-test mutation retaining armed references so finalizers cannot hide omitted cleanup. |

Author fresh-eyes review also compared all nine changed plans with the
captured drafts, removed the stale instructions identified by the review,
and reconciled the class and plan-number entries. Documentation
verification: `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q`
passes all eight checks; the two initial metadata failures on the CLI plan
are corrected. `git diff --check` and whitespace checks of all nine changed
plans pass. No spec was promoted and no runtime implementation is claimed.

**Round 1 — 2026-08-31, independent adversarial review of the first
draft.** One blocker (B1: the `_register_manager` supersede re-check is
load-bearing for `--replace` versus an in-flight heartbeat; deletion
withdrawn) and seven should-fixes, all applied to the workstreams that
became child plans 2–4 (envelope ordering, retirement gate, success-ack
sites and `cleanup_on_exit` mapping, accepted stale-row behavior, the
nonexistent ambiguity error, reopen-time tests, [MA-1] wording scoped to
deletion).

**Round 2 — 2026-08-31, second independent review plus code-level
verification of each contested claim.** Confirmed: Class 5 not 4; [PY-2]
forbids the `stop_many` change; [MF-3.1] and [MA-1.4] mandate the
reader-side pruning the draft proposed removing (so those are spec deltas);
[SB-0.4a] forbids the WS9 quarantine (dropped); interactive session-start
failure and ack ordering; managed-PID custody defect (confirmed end to end
and reproduced); short-TID contract gap. Rejected: the collation-retirement
"ordering" bug (predicate-gated); "delete the discovery throttle" (fix the
reset instead). Found during verification, listed by nobody: persistent
named tasks lose their endpoint claim after the first work item.

**Round 3 — 2026-08-31, owner review of REQUEUE and the store toggle.**
Van: `requeue` was never implemented in the sense that matters; remove
from task specs. Van: a design in which the TaskMonitor does not collate
and delete is unlikely to be correct — verified that the disabled-store
mode already is such a design; remove the toggle, keep `report_only`.

**Round 9 — 2026-09-08, implementation review of the seven landed
commits (caa1513b, 0b20d4d1, f3ad6741, 296cfa61, fa14cd05, ee440fa5,
b605582c; 2026-09-07).** Six same-family conformance reviewers, one per
commit (plans 6 and 7 shared), each checking plan tasks, promoted delta
text, owner decisions, named tests, added mechanism, and surviving
deleters; every finding below was re-verified in code by the author
before recording. **No blocker.** Every owner decision holds in code:
Manager REQUEUE in full; task `requeue` rejected at the three public
surfaces and at `BaseTask.__init__` via `_allowed_reserved_policies`;
all backstop drains gone with the `internal_reserved` cleanups kept;
readers never delete; `_prune_managed_service_registry_history` deleted
with no id store; pruner passes the 300 s TTL; unknown owners pruned past
both windows with no probe; malformed rows pruned and logged at ERROR
through `send_log`; readers omit expired unknown rows without PING; one
liveness reducer for both sites; `_register_manager` untouched; one-shot
endpoint registration; one mapping fold with all eight consumers
repointed; short-TID ambiguity fails before any control write; the
logging facade, `add_queue`/`remove_queue`, `yield_strategy`, and the
`create_runner` capability checks kept; the store always on with the
engine gone; the folded short form exactly as specified. Gates on HEAD:
ruff, mypy (187 files), spec/plan/metadata gates (the only failure is the
unrelated 2026-09-02 plan), suppression `--check`, and the fast suite at
two workers (0 failures); at the default 12 workers five tests failed
under host load 21 and all five pass in isolation — the known structural
flakiness, not a regression.

Code findings (owner decisions, none blocking):

| # | Commit | Finding | Suggested disposition |
|---|--------|---------|-----------------------|
| 1 | caa1513b | `HostTaskRunner.run_with_hooks` raises `_require_worker_reaped` in its `finally` after `return outcome`, so a worker that delivered its result but survives terminate/terminate/kill with 0.2 s joins each (~0.6 s) turns an `ok` item into `work_failed` | decide whether reap failure should fail a delivered result; if not, log and keep the outcome |
| 2 | caa1513b | `_merge_host_process_observations` now `update()`s recorded values, so a recorded `None` create_time overrides the handle's known exact identity (pre-plan `dict.get` kept it); reachable when a fast worker exits before `register_managed_pid` reads its create time | do not let `None` override a known value (one-line) |
| 3 | fa14cd05 | `dump_system` passes `context.root`; `cmd_system_dump` rebuilds the context, so a client with a `WEFT_DIRECTORY_NAME` override gets a different default export path than before — the exact pattern the same commit's lesson and `_serve_manager_context` fix address | apply the resolved-context pattern to `dump_system`/`tidy_system` |
| 4 | 296cfa61 | a new normative stop-confirmation paragraph (03-Manager_Architecture.md ~:566) and its `_await_manager_stop_confirmation` guard were promoted outside the Codex-reviewed delta; a missing/invalid handle now reduces to `unknown` (was definitively stale), so such rows persist until both windows pass | confirm the contract; add both to plan 4's Deviation Log |
| 5 | f3ad6741 | interactive exit: the client waits 1.0 s for the STOP ack while the task acks only after `INTERACTIVE_STOP_GRACE_SECONDS` = 2.0 s, so a child lingering past 1 s now draws an extra KILL request/ack pair and exits 1–2 s later — an observable difference | register (class C) or align the client wait with the grace |
| 6 | 296cfa61 | foreground serve now preserves an unconfirmed external-supervisor record instead of writing `superseded` (test renamed) — observable difference not in §4a | owner confirm; register |
| 7 | 0b20d4d1 | Manager CLEAR-failure logs at DEBUG while the task side logs WARNING, so preserved Manager residue is invisible at default log level | raise to WARNING |

Record hygiene (fix in a docs pass): all seven Deviation Log tables are
empty while each Implementation Record narrates deviations (plan 5's §5.8
still asserts a Docker/macOS `create_runner` gate that never existed);
five of seven commit bodies are empty, plan 5's mandated zero-caller grep
record is absent, and plans 2/4 did not list test dispositions by name;
`docs/ruff-suppression-registry.md` rows SUP-005, SUP-017, SUP-025 cite
deleted or renamed tests (the policy test does not check citations);
05-Message_Flow_and_State.md:275 names `InteractiveSessionMixin` (class is
`InteractiveTaskMixin`); 01-Core_Components.md:539 backlink sits on the
Debugger bullet; 00-Quick_Reference.md:255 and [CC-2.4.1] carry sentences
outside the deltas; inert plumbing left behind (`probe_stale` threaded to
a `del`, `manager_registry_record_is_stale` and
`RUNTIME_PRUNE_CLASS_SUPERSEDED_MANAGER` unused, `families_retired` always
0, `TASK_MONITOR_TASK_LOG_SELECTION_LIMIT_REACHED` and
`TASK_LOG_START_EVENTS` unreferenced). Post-implementation review was
same-family only on every plan; this ledger is not yet committed.

**Round 8 — 2026-09-04, Codex third review of plan 4 (after the
reconciliation amendments; ~5.6M tokens).** Direction held again; four
contract blockers, all verified and applied: the reconciliation's
external-supervisor age branch contradicted [MA-1]'s `unknown` (fixed by
removing the branch and giving `unknown` one bounded PING-at-prune-time
rule — owner-confirm); four mapping-row consumers outside the fold
inventory, one of which strips [OBS.13.7] protection on a malformed newer
row; `short` "display-only" versus required row shape; malformed
service-row deletion unauthorized by [MF-5] (now a named class with
deltas — owner-confirm). Should-fixes: the pruner's age-only
superseded-manager arm goes (owner prunes own history), helper before
consumers, [PY-2] batch semantics stated, prune test file added, five
spec files, and the `_register_manager` replay optimization dropped as
custody-unrelated risk.

**Round 7 — 2026-09-04, retirement-rule reconciliation (author pass,
verified in code; Codex third review of plan 4 follows).** Every
deletion or retirement path in `weft/` was enumerated (task-log raw rows
by engine selection class, store families, task-local queue slices,
reserved-row disposition, tid mappings, the four service-registry
deleters, endpoints, streaming, pipelines, spawn acks, control-reply
rows) and mapped to its rule under plans 1–7. Twenty-two keep a rule.
Plan 3 adds coverage (with the store off, no task-local cleanup ran at
all); plan 2's removed success-path drains are covered by the reserved
slice's name-scan fallback, which selects every `T*.reserved` queue after
cleanup proof and retention age regardless of `reserved_probe_needed`;
plan 5's dead pipeline retires nothing today. Three gaps, all in plan 4
and all amended there: `external-supervisor` manager rows would never
have retired under "definitively stale"; managed-service rows with lost
custody had no deleter under the retained-id store (which is dropped —
the pruner passes the reader TTL instead, one argument, no new state);
`unknown` owners have no time bound (open decision). Complexity check:
the only mechanism the plans would have added to retirement was that id
store; it is gone. The remaining additions are the single-owner
predicate for non-`active` manager rows (without it terminal rows have
no deleter) and the tri-state identity reduction (correctness).

**Round 6 — 2026-08-31, Codex second reviews of the revised plans 1 and
4.** Both still blocked, on implementability rather than direction: plan 1
needed a reap discriminator (`RunnerOutcome` has no reap fact), the host
plugin's own stop/kill path in scope (same check-then-reconstruct race),
[CC-3.2]-faithful authority wording, host-only scope, a valid handle
`kind`, and review-before-promotion order (now fixed in all four Class-5
plans); plan 4's min-age-only terminal pruning could have deleted live
supersession authority, its "valid row" domain conflicted with a raising
helper, its tri-state needed an explicit reduction, and the folded
derivation was split into plan 7. All dispositioned in the plans.

**Round 5 — 2026-08-31, Codex (cross-model) pre-promotion reviews of
plans 2, 4, 5, 6 and pre-implementation review of plan 1 (5 sessions,
~21M tokens).** Every plan's core decision held; every plan was judged
not promotable as first written; all findings dispositioned in each
plan's Review Record. Cross-cutting: plan 4's [OBS.5] was built on a
wrong SimpleBroker timestamp model (author error — bit-width inference
instead of reading the encoder); plan 6's `report_only` sentence was
false under `raw_external` ownership and a second env-var rejection site
was missed; plan 2's public validation owner claim was wrong (two owners)
and the manager spec does cross the transport boundary; plan 5's
"unspecced" status-JSON call was wrong ([CLI-1.2.1] requires strings)
and `log_debug` had a live caller; plan 1 needed a Protocol change it
forbade and lacked a normative post-reap rule. Codex also corrected the
Claude round in two places (retire-note in an immutable completed plan;
Manager launch-failure REQUEUE). Cross-family review earned its place.

**Round 4 — 2026-08-31, independent pre-promotion reviews of the four
Class-5 deltas (plans 2, 4, 5, 6), one Claude-family reviewer each — no cross-family (Codex) review has been run on any plan yet.** Every plan's
core decision held; each review's findings are dispositioned in that
plan's own Review Record. Cross-cutting outcomes: plan 6's draft delta
would have deleted live `raw_external` PONG diagnostics (kept); plan 4
would have left terminal manager rows with no deleter (pruner predicate
added) and would have made every crashed-manager row cost a 0.5 s probe
per read (identity-mismatch is now definitively stale); plan 2's
launch-failure REQUEUE question was decided by Van — Manager REQUEUE
kept in full — and the review surfaced an unguarded same-TID relaunch
path via `Client.submit` (prohibited in the [MF-2] delta; API guard is a
follow-up); plan 5 had no blocker but five spec mapping blocks and the
ruff suppression registry must move with the deletions. Owner decision
recorded: `MultiQueueWatcher` dynamic topology is kept (external
embedders); `weft/core/tasks/` deletions are owner-confirm.

## Out of Scope

- Any SimpleBroker-layer change; any new abstraction.
- `MultiQueueWatcher` dynamic topology ([QUEUE.8]) — kept by owner decision.
- Behavior changes to admission control, leadership selection outcomes, or
  public CLI shapes.
