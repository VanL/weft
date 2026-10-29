# Measured Manager Dispatch Batching

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.1], [MA-1.2], [MA-1.8], [MA-3]; docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-6]; docs/specifications/07-System_Invariants.md [MANAGER.11], [MANAGER.18], [IMPL.8], [IMPL.10]
Superseded by: none

Class: 5. Implementation with spec revision; hardening applies to reactor control order, launch custody, and cleanup. The October 27 revision replaces the concurrent-worker-first sequence with measured serial dispatch improvements. This document authorizes no deployment. Plan authoring does not implement or promote the proposed behavior.

## Goal

Measure and reduce repeated Manager dispatch work while retaining the existing single launch-worker lane. First isolate duplicate child-sweep removal, then receipt-turn continuation and bounded serial dispatch batching. Consider concurrent launch workers only after those comparisons establish a remaining process-creation bottleneck worth their additional ownership and recovery machinery. A measured no-change or smaller-change result is a valid outcome.

## Spec Baseline and Source Documents

Baseline: `e220748f786ea72dad00fbc7672fc484e24bef0c` for runtime and governing specifications. Record a promotion baseline identifier when each accepted delta lands. Active specifications remain normative until promotion; the historical October 22 review below is not authorization to implement its earlier concurrency design.

Read before implementation:

- `docs/program-theory.md` [WT-2], [WT-4], [ALT-WT-001], [ALT-WT-002], [ALT-WT-015], [ALT-WT-017], [ALT-WT-019]: measured admission, one reactor owner, detached lineage, no guessed task charges or child lane, no fallback polling cap, normal-concurrency verification, queues as consumable durable facts.
- The exact Source specs above. In particular, retain the receipt/handoff requirement before the next ordinary dispatch in both [MA-1.8] and [MANAGER.18]. This serial plan does not need the former draft's concurrent in-flight custody exception.
- `docs/plans/2026-10-20-bounded-startup-fast-path.md` and `docs/plans/2026-10-15-work-conserving-family-scheduling.md`: implemented predecessor behavior remains intended. Startup exposure is not worker concurrency; pairwise frontier normalization and full checkpoint costs were already measured and deferred.
- `docs/lessons.md` May reactor lesson: results must not let new dispatch bypass controls. Preserve that reason while deliberately revising the receipt-consumes-turn prescription.
- `docs/agent-context/runbooks/writing-plans.md`, `docs/agent-context/runbooks/hardening-plans.md`, `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`, `docs/agent-context/runbooks/testing-patterns.md`, `docs/agent-context/runbooks/adversarial-acceptance-probes.md`, and `skills/test-audit/SKILL.md`.

The separate [runtime and test repair plan](./2026-10-27-runtime-and-test-repair-plan.md) owns the confirmed command-lifecycle, heap-history, and test-sensitivity defects. Keep its code changes out of the admission comparison, or establish a fresh common baseline after they land.

## Verified Premise and Measurement Limits

October 27 production-path diagnostic: PostgreSQL 18, server connection ceiling 300, isolated disposable server, real `WeftTestHarness`, 12 prequeued `family_enrichment` parents and their 12 `family_leaf` children, built-in autostart disabled for that diagnostic. The clean repeat completed the parents and leaves in 5.920 seconds. It observed 88 reactor turns, 4.355 seconds in turn spans, 24 process-launch spans totaling 0.101 seconds (4.21 ms mean), 0.404 seconds in admission spans, and 0.422 seconds in waiter-setup spans. Parent start spacing averaged 328 ms and maximum observed unfinished-startup membership was four. An earlier run had similar launch/admission costs and 265 ms mean parent spacing. These are overlapping spans; never sum them or call turn time wasted time. This is a local diagnostic, not a throughput forecast or an optimization acceptance result.

The disposable diagnostic outputs were `/tmp/weft-review-launch-phases-repeat.log`, `/tmp/weft-review-launch-phases-repeat.jsonl`, and `/tmp/weft-review-launch-call-profile.jsonl`. They are optional historical evidence, not required implementation inputs. Reconstruct the workload in the repository test harness in Slice 1 and record runnable commands/results here. Observer wrappers must preserve production calls and outcomes. Do not substitute stub admission, queues, checkpointing, process creation, or results.

Current code confirms the causal candidates:

- `Manager._process_manager_reactor_turn` performs turn-level `_cleanup_children`; `_handle_work_message` performs another sweep during delivery. The second call can also perform lifecycle commits inside a queue pass.
- `_handle_child_launch_result` sets `_child_launch_started_this_turn`, consuming the turn allowance even when only a previous launch's receipt was handled. `_process_queue_message` and `_drain_spawn_requests_from_queue` honor that flag as well as the active-launch gate.
- `BaseTask._process_reactor_turn` drains worker results before and after `_drain_queue`. A receipt can therefore be available at a safe, post-delivery boundary. It is not necessarily available: serial batching must never wait, spin, or create another worker to manufacture a batch.
- `_launch_child_task` uses one registered `ServiceTask` launch group. Its existing result commit registers family lineage before another request can be selected. Keeping this ordering avoids provisional selection state and out-of-order receipts.
- `MultiQueueWatcher._ensure_multi_activity_waiter` already caches its topology signature. Setup cost is evidence to investigate, not proof that unchanged waiters are rebuilt.

## Ownership, Invariants, and Difference Register

The reactor alone owns scheduling, admission, exact reservation, inbox seeding, checkpoint publication, TaskSpec/state effects, and acknowledgment. The existing broker-free worker owns process creation. The pending backlog remains in broker queues; no worker-local reservoir is added.

Preserve TIDs, immutable spec/io, forward-only state, existing public/internal queues and priority, spawn context, runtime-state dump exclusions, reserved policies, and detached user-task survival. Preserve PostgreSQL startup window W=6 with H=floor(4(C-R)/5), its pressure fallback, and SQLite's serialized startup. Fresh coherent usage is required before each eligible reservation; unknown usage denies. An empty worker lane is not resource authority. Positive admission is not cached across launches.

The final pre-seed control check remains the launch authorization boundary. After seed begins, keep custody through checkpointing, worker handoff, receipt and exact settlement. Do not add a post-seed STOP requeue/reseed path. Failed checkpoint publication and unknown execution block ordinary dispatch. Preserve task-owned snapshot/positive settlement or successful post-launch checkpoint before the next ordinary dispatch. Startup readiness alone never acknowledges a reservation. Missing process evidence is not death.

| Current distinction | Disposition in serial work | Reason / proof |
|---|---|---|
| Turn-level sweep plus handler sweep | Skip handler sweep only inside a reactor-owned delivery scope whose turn sweep already ran; preserve standalone/shutdown calls | Count actual calls and prove ready exits/retries cause normal settlement before another authorization |
| Receipt marks turn allowance consumed | Preserve B at initial entry; only a new submission settled within the preceding ordinary pass can qualify one extra C2 pass | Existing custody/STOP fences remain; actual submission and settled active map prove receipt ownership |
| One new child launch per turn | Retain for B and C1; C2 alone adds attempted-authorization accounting at safe post-delivery boundaries | No receipt waiting or overlapping process creation; real traces must show batches actually occur |
| Family lineage registered on receipt | Preserve | With serial launch custody, receipt commits precede next selection; no provisional scheduler state is needed |
| Existing bounded individual control/result drain calls | Retain their per-call limits; C2 adds at most one complete pass and requires an observed-empty control drain before it | No new aggregate quota or starvation of first-pass work; finite pass structure bounds added work |
| Priority watcher rejection loop has no aggregate work counter | C2 adds Manager-owned attempted spawn-delivery counters using existing public/internal lane ceilings | Check before fetch/reservation through the shared Manager gate; count malformed/rejected attempts and retain continuation on exhaustion |
| A full classification/repair page retains continuation | Preserve known-work dispatch after that turn-start page; continuation may end extra C2 passes | Do not drain the whole backlog before offering an already-indexed eligible request |
| SQLite startup | Preserve one new child launch per turn and one unfinished startup | PostgreSQL batching is not permission to change SQLite semantics |
| Internal priority and reserve | Preserve on each eligible selection | Pending internal work wins at next free lane; no special child lane or new resource class |
| Positive startup/publication wake vs missing evidence retry | Preserve event wake and bounded absence deadline separately | Do not lower fallback timers to hide missing readiness continuation |

No new public configuration, dependencies, broker API, durable format, launch group, selective cleanup API, persistent worker pool, or global admission guarantee is introduced by the serial slices. Quadratic frontier normalization, heap reclamation, TaskMonitor clocks, and unrelated waiter redesign are outside this plan.

## Experiment Arms and Decision Rules

Use the same fixed submitted workload, server capacity, resource configuration, result assertions, and standard pytest worker configuration for each comparison. Keep built-in autostart settings identical within a comparison. Also run the original system workload with its unchanged autostart, concurrency, and capacity settings; the minimal diagnostic is not a substitute for that acceptance. Warm once, then record at least three measured repetitions per retained arm with alternating order where practical. Report spread and raw samples, not just the best run. A correctness failure is not cured by reducing load, increasing timeouts/capacity, or lowering xdist workers.

| Arm | Production delta | Attribution question |
|---|---|---|
| A | Current single lane, receipt consumes turn, duplicate sweep | Reproducible baseline |
| B | A plus dispatch-scoped duplicate-sweep removal only | Does one fewer sweep per dispatched request reduce work without delaying required settlement? |
| C1 (rejected) | B plus initial-result-phase receipt continuation, still one new child launch per turn | Does eliminating the receipt-only turn reduce turns and latency? |
| C2 (plan approved; implementation and measurement pending) | Retained B plus at most two serial delivery passes on PostgreSQL, one on SQLite | Are receipts ready soon enough to share maintenance within an outer turn? Is the extra loop worthwhile? |
| D (deferred) | Concurrent launch workers | Not an implementation arm until the later gate and a fresh reviewed spec/design revision |

The selected C2 experiment uses an internal PostgreSQL pass limit of two and SQLite limit of one, with the constant owned by `weft/_constants.py`. These are not user knobs or startup-window settings. Candidates four and six are not authorized by this revision. C1 was measured and rejected; its initial-receipt exception is not part of C2. C2 is removed unless the existing elapsed-time and control gates pass.

Record per arm: useful completions and elapsed time; enqueue-to-start and completion-to-next-authorized-start distributions; PING/STOP observation latency; turns and authorizations per turn; sweep calls and maintenance yields per authorization; yield-turn counts; inclusive/exclusive phase times with nesting declared; actual admission observations and checkpoint publications; worker-lane and startup membership maxima; global observed pressure; and time blocked by pressure, startup exposure, active launch, or uncertain custody. Use existing timing hooks and observer-only test wrappers. Preserve causal order and IDs; if block-reason intervals require instrumentation, keep it test-local unless an independently justified production diagnostic is needed.

Keep B only with the scope/freshness proofs and without a meaningful reproducible latency/control regression. Count maintenance-yield turns and yields per authorization alongside removed sweeps. Neutral timing may still justify removing redundant lifecycle work; if B increases latency materially, remove it or bring the measured tradeoff to the owner before retaining it. Keep C1/C2 only when repeated measurements show fewer redundant turns or meaningful latency/completion gain without control, custody, or maintenance regressions. Do not hardcode machine-specific throughput percentages into correctness tests. Record a no-benefit result and remove an unhelpful C2 loop rather than leaving dormant switches.

Predeclared comparison rule: the primary performance series are quiet-mode
useful-work elapsed, canonical PING call-to-return latency, and STOP write-to-
handler-return latency. Other series, including trace enqueue-to-start and
completion-to-next-authorized-start medians/p95, start spacing, drain/cleanup
spans and operation counts, are secondary explanations and safety evidence;
do not search them for a convenient speed win. A safety/custody/control failure
rejects an arm regardless of timing. Trace overhead is not assumed neutral.

Use A/B/A batch order on each backend and mode: each batch has one warmup and
three measured repetitions with the same server lifetime. Retain the first A
before implementing B. A primary-series regression is reproducible when all
three B values exceed the maximum of both surrounding A batches. A claimed
speed benefit must satisfy the symmetric rule on **quiet useful-work elapsed**:
all three candidate values lie below the minimum of both surrounding baseline
batches. Require claimed speed benefit on PostgreSQL; both PostgreSQL and SQLite
must preserve the primary quiet nonregression gates. SQLite remains serialized.
Apply B/C1/B to C1, and the equivalent surrounding baseline order to
any C2 candidate. Matched repetition ordinals remain in the report, but add no
independent criterion to the min/max rule. Disjoint surrounding baseline ranges
indicate drift, so attribution remains uncertain. These are conservative
experiment decisions, not statistical significance claims; the series are
correlated and multiple comparisons remain a limitation.

If ranges overlap or controls are ambiguous, allow at most one additional full
surrounding-baseline batch set for that comparison, declared now, then report
uncertainty rather than changing thresholds or collecting until a win appears.
B may retain a proven redundant-sweep reduction with neutral quiet timing and
no demonstrated primary regression. C1 may retain a proven reduction in
receipt-only turns on PostgreSQL with neutral quiet timing and preserved
control/custody on both backends. Require all three candidate receipt-only-turn
counts below both surrounding baseline minima, using trace-mode complete observed
turns. Every compared sample must have zero censored receipt turns; otherwise
this structural-benefit evidence is unavailable. Otherwise its speed claim needs the symmetric elapsed-time rule. C2 must show
that elapsed-time benefit and preserve all primary control series, or its loop
is removed. A smaller operation count never excuses a primary quiet regression.
This rule is not a machine-specific pytest assertion. Retain raw samples,
missing/censored counts, yields and blocked-reason evidence. B trace batches
must demonstrate actual gate-predicate observations; zero observed gate calls
means the maintenance metrics are unavailable, not proof of zero yields, and
cannot close the B measurement gate until instrumentation coverage is resolved.


The repeated measurement cases are explicit opt-in experiments, not routine
correctness coverage. At collection, skip them only when
`PYTEST_WEFT_DISPATCH_ARTIFACT_DIR` is absent, before provisioning their backend
fixture. A present empty, relative or unwritable destination is a configuration
failure, not a skip. Measurement commands must select one quiet/trace case at a
time, use a fresh destination parent for each invocation, and require a passing case plus complete retained warmup, three measured
repetitions and raw artifacts. A skipped experiment is never a passing
measurement gate. Existing six/eight-parent correctness tests remain unchanged
in routine CI. This avoids multiplying repeated benchmark work across the full
platform matrix while preserving explicit measurement and correctness gates.

Measurement interpretation and control (declared before the first run): trace
spans and observer wrappers have unmeasured overhead, which does not necessarily
cancel across arms that remove or add observed work. Run a separate **quiet
control** mode through the same fixed workload, harness, result assertions and
control/custody lifecycle, with default logging. Retain only forwarding hooks
at first parent launch receipt, matching control handling and STOP completion to
preserve the causal probe placement. This is reduced instrumentation, not an
unqualified uninstrumented claim. Invoke quiet and trace node IDs separately,
with normal worker configuration, and apply the A/B/A (later B/C1/B) batch rule
independently to each mode. Compare quiet useful-work elapsed and canonical PING
call-to-return/STOP observation latency; trace data supplies phase/count and
latency-distribution attribution. A reproducible quiet regression cannot be
excused by better traced counts or spans. Missing quiet internal metrics are
unavailable, not zero, and detailed post-STOP authorization checks remain the
trace mode's proof rather than a vacuous quiet pass.

Both modes start the same useful-work clock before the control thread and
foreground Manager start, including normal autostart/convergence; stop it after
all fixed parent results validate their leaves, before control-thread joins or
cleanup. Canonical PING call-to-return includes its real setup and retirement;
trace write-to-matched-PONG is a separate measurement. STOP handling, drain
completion and cleanup remain separate intervals. The completion-to-next-start
metric means the next actually authorized user launch after a parent completion;
it establishes eligibility at that later authorization, not continuous eligibility
since completion. Missing later launches are censored; report counts. Blocked
intervals are overlapping, sample-held estimates. Nearest-rank p95 for this small
workload is near the maximum, so retain raw samples and sample sizes. Before the four scenario repetitions, run a small source-provenance task
inside the actual pytest invocation, using a separate normal WeftTestHarness
and the real foreground Manager, Consumer and function-worker path. Compare
the child-reported Manager/family module paths and hashes with
the current checkout, verify child identity/interpreter, then clean up that
preflight harness. Write its verified result as `source-provenance.json` in the
batch and require it in completion. Missing or mismatched proof fails before
samples. This extra preflight is outside every measured window and identical
across arms; it does not replace the declared warmup. Parent manifests alone
are insufficient. The earlier standalone probe is an unused diagnostic artifact,
not the gate, avoiding a separate-entry/environment equivalence assumption.

Concurrent-worker gate: after choosing the smallest serial result, show that eligible requests repeatedly wait specifically for process creation, with fresh capacity/startup headroom and no custody/control blocker; measure worker occupancy and the available overlap on supported runtimes. The 101 ms historical process-launch total alone does not satisfy this gate. Report the added failure surfaces and expected bounded benefit. If evidence is insufficient, close the serial work without adding workers. If sufficient, revise this plan or create an indexed successor with exact concurrent custody/ordering deltas, receive cross-family review, promote those deltas, and only then implement concurrency. No automatic jump from C2 to multiple workers.

## Serial Dispatch Design and Maintenance Boundaries

B uses an explicit reactor-owned delivery-scope flag with `try/finally`. Set it only around the existing BaseTask delivery and explicit public-drain paths after turn-level cleanup and any forced convergence have completed. `_handle_work_message` skips its sweep only in that scope; standalone handler calls, service convergence and shutdown keep their cleanup. Clear the scope on exceptions. Before each spawn delivery in the scope, including the first, a ready child-sentinel observation (`_child_sentinel_adapter.wait(0)`) or a due child-maintenance deadline from `_manager_child_timeouts` yields to an immediate ordinary turn. That turn drains the sentinel hint and performs normal settlement; the gate does not consume observations or perform child cleanup. Existing sentinel wake and due-deadline readiness suffice; do not add a redundant wake call. Existing later service convergence may settle the yielded child in the same outer turn. Assert nonconsumption at the gate return and ordinary settlement before subsequent authorization, not that the child must survive until turn end; B does not change convergence ordering. Place the check after cheap startup/candidate early returns but before admission and fetch/reservation. A sentinel hint is not process-death evidence. B retains one new child launch per turn and receipt-consumes-turn behavior. Other maintenance owners retain their existing phases and deadlines; no blanket maintenance-due veto is introduced. A bounded classification/source-repair page may retain continuation while known eligible work receives its normal first dispatch; preserve its continuation wake. C2 reuses this same per-delivery child gate across its bounded passes; other maintenance owners retain their existing tail phases.

B's deadline predicate relies on `_cleanup_children` advancing or retiring each child deadline it services. Sentinel-recheck and proof-grace deadlines already do so; S2 makes publication retry do the same. Repair S2 in `2026-10-27-runtime-and-test-repair-plan.md` clears a consumed terminal-publication retry before entering fresh proof grace, while preserving future retries, custody, and failed-publication rearming. It restores existing [MA-1.6a] no-spin behavior and belongs in the common repaired A/B baseline, not in B's measured delta. Include the real clean-exit/read-and-write-outage/recovery regression; a killed wrapper alone does not cover this transition.

C1 is closed as rejected. Its reset relocation, provisional spec and lesson changes, and six candidate-specific tests were restored to B after the verified B/C1/B comparison. The pressure-test setup correction remains independently valid. Historical C1 design and review records below are evidence, not instructions to reintroduce it.

## Selected B+C2 algorithm

Retain B's initial allowance reset **before** initial result handling. Do not retain C1's initial-receipt exception. Use at most two complete ordinary delivery passes on PostgreSQL and one on SQLite. No new result drain, aggregate result counter, aggregate control counter, control-handler override, broad maintenance predicate, service-readiness extraction, startup-publication check or stale-launch check.

Keep a cohesive optional `_ManagerDispatchBudget` record containing only **internal attempts remaining, public attempts remaining, submitted_in_pass**. Initialize the two counters to existing128 lane ceilings at outer normal-turn entry; clear record in `finally`. It covers inherited watcher and explicit/convergence delivery paths. No authorization counter is necessary because the following structure bounds attempted authorization:

1. Every production `_launch_child_task` call is from `_handle_work_message`; its two queue-config bindings run through `_process_queue_message`. The existing `_launch_child_task` owner closes its allowance at entry, before draining/seed/class/checkpoint/start can fail. This relocates its previous later write instead of adding another write in the handler. A standalone failed call also leaves the flag closed; the next normal turn resets it before dispatch, and direct launch calls do not consult it. Verify standalone launch and handler cases rather than claiming the private flag is unchanged.
2. Extract the existing ordinary delivery body into `_process_manager_delivery_pass` if needed to repeat it without copying. First pass does **not** clear the flag. A preceding forced-convergence attempt or initial old receipt has already closed it and therefore blocks ordinary authorization. Reset only `submitted_in_pass=False` before the ordinary pass, so prior forced work cannot qualify an extra pass.
3. Set `submitted_in_pass=True` in `_launch_child_task` immediately before its successful return, only **inside ordinary delivery scope** with a live budget. At complete-pass return, require submittedTrue, empty Manager active-launch map, not stop/draining/pending termination (`_has_pending_termination_request()`), and no `_family_classification_pending`. Otherwise stop. There is no wait or additional result drain to obtain a receipt.
4. Before the sole possible extra PG pass, call the existing bounded control drain. It must report that it actually reached empty. Recheck stop/draining/termination, active map and classification continuation. Only then clear the allowance flag and run one more complete ordinary pass.
5. The extra pass has a `try/finally` that unconditionally recloses the allowance flag. Its grant belongs only to ordinary delivery. If the second pass attempted a launch, the pre-seed write already closed its allowance. Reclosure is necessary when admission denied the second attempt: it defers a possible second attempt in tail convergence to the next turn. This conservative phase boundary can delay late internal work even when internal admission still has headroom; it is an explicit policy cost, not a claim that every missing reclosure creates a third launch. The loop is literally bounded by backend pass count2/1; no separate extra-pass-used boolean is needed.
6. Keep B's `_dispatch_cleanup_scope` true across both ordinary passes and clear it in `finally`. Its existing per-delivery sentinel/child-deadline gate protects the second reservation. Leave the normal tail leadership/convergence phases exactly where they are. They may be delayed by one bounded pass, which is an explicit C2 policy tradeoff tested by PING/STOP and timing, not a hidden safety claim.

Structural attempt proof: each complete pass closes the flag before its first seed, so it cannot attempt twice. Fixed pass count is2/1. Forced convergence before ordinary delivery uses the same handler and closes the first-pass flag; because first pass never clears it and has submittedFalse, it cannot open an extra pass. Tail convergence inherits a closed flag after a first attempt or after the extra-pass finally. If ordinary work never attempted, baseline tail behavior can still attempt once; no extra ordinary pass was opened. There are no other production launch callers or direct work-handler calls bypassing the shared queue gate. Source inventory: sole launch call in `_handle_work_message`; `_build_queue_configs` binds handler to public/internal; forced convergence occurs before scope after outer cleanup, tail convergence after scope. `weft/commands/_spawn_submission.py::_handle_work_message` only normalizes migration delivery and delegates to the Manager handler; it adds no launch path. Do not add an API for direct mid-turn private-handler calls; no production caller does that.

Receipt proof remains successful submittedTrue + empty active map within that pass. Sync failure returnsFalse; stale recovery runs before pass; final cleanup runs after active owner turn; all normal/error/invalid worker results clear through receipt handling. ServiceTask retirement alone does not clear Manager's map. A failed worker receipt can qualify: submission succeeded, its real receipt settled custody, and the next attempt still passes normal retry/admission gates. Successful submission does not mean successful child launch. Explicit public fallback at the end of a complete pass has no subsequent result drain, so its submission cannot qualify an extra pass merely because the worker has published a result. No receipt count or TID map.

Preserve known active sources **only after a successful submission in this ordinary pass**: the shared busy/closed-allowance gate skips `inactive_candidates.add` when `_dispatch_cleanup_scope and budget.submitted_in_pass`. Earlier failures and outside-scope behavior retain B's removal. This retains internal priority for pass2 without changing unrelated failed-seed source hints. Existing priority loop stops on processed0. `_resume_startup_dispatch` and all wake paths remain unchanged.

Keep trace emission untouched. The existing `launch_started_this_turn` field already means an allowance fence (receipts set it). C2 extends its closed condition to preseed attempts and temporarily resets it for pass2; document that it is not a count and never use it as the authorization/receipt oracle. Actual event IDs and complete turn enclosures remain the measurement authority.

## Internal quota priority and the classified frontier

At the shared public prefetch gate, if the outer-turn internal attempted-fetch allowance is exhausted, deny public fetch/reservation while internal pending state is true or unknown. Do not mark public inactive on this deferral. Positively observed empty permits normal public gates. Reuse `_internal_spawn_pending(*, unknown_is_pending: bool = False)`: retain False for absent internal queue and successful empty observation, return the keyword on its existing `(BrokerError, OSError, RuntimeError)` catch. Only the new quota guard passes True. Existing service-convergence callers retain their default False and existing behavior. This is one existing queue read with explicit failure policy, not a tri-state protocol or duplicate broker probe. Read at the quota boundary rather than caching emptiness; like existing priority observations it cannot promise atomic exclusion of later arrivals.

Pass2 reuses the indexed frontier established by normal turn-start classification, updated by actual receipt/removal/requeue handling. It adds no classification refill. [MA-1.8] specifies “dynamically observed ancestry”, “known descendants”, and “eligible indexed work”; it does not require rediscovering all arrivals before every authorization. Clarify this scope in the proposed contract below: an unclassified late descendant may wait through one additional independent authorization, while known indexed descendants retain precedence. Classification continuation still vetoes the extra pass. This is an explicit bounded discovery-delay tradeoff, not an exception to ordering among indexed work.

## Boundary control drain returns evidence, not a new global quota

Change `_drain_control_queue_first` return annotation to bool. ReturnTrue only when an existing peek proves empty: initial `pending is None`, or the existing post-handle `next_pending is None`. Both empty observations must call `_clear_stalled_control_message()` before returningTrue, so a resolved stalled row leaves no stale retry deadline. ReturnFalse on stop/draining, stalled/nonadvancing row, invalid timestamp/shape, or exhausting32 while a row remains. No extra queue read is required. Its initial-turn and `_control_allows_child_launch` callers ignore the result, preserving baseline control handling. Only the new extra-pass boundary requiresTrue.

Thus33 initial PINGs still permit the baseline first launch (32 initial, remaining one at handler fence), without aggregate-cap-induced reservation churn. More than32 controls newly queued at the extra boundary postpone only the extra pass; the existing first pass is not starved. STOP is still checked at both existing handler fences, and boundary emptiness is not an atomic promise against future control arrival.


## Test inventory and changes

Read-only inventory covered `tests/core/test_manager.py`, `test_manager_family_maintenance.py`, `test_admission_recovery.py`, `test_manager_burst_bookkeeping.py`, `test_family_dispatch_scaling.py`, `test_child_spawn_validation.py`, `tests/system/test_measured_admission_pg.py`, and direct Manager-turn callers in task tests. Exact source names below, not a claim that tests were executed.

**Must change/remove:**

- Consolidate `test_manager_stop_command_does_not_launch_new_children_after_stop` into `test_manager_services_control_while_real_launch_receipt_is_held[STOP]`: hold the actual worker, deliver real STOP and prove exact successor nonreservation and later cleanup. The old first-turn count and log-order checks are retired; real custody, control and blocked-worker assertions replace them. C2 batch size belongs in the batch witness.
- `test_manager_stops_spawn_drain_after_child_launch_starts` (currently3256): fake `_launch_child_task` returnsTrue and sets only the flag, with an empty active map. That fabricates C2's settlement condition. Replace fake with real held worker/receipt and assert successor stays reserved nowhere while lane is occupied. This can be the C2 held-worker/PING family rather than a duplicate.
- `test_manager_processes_internal_spawn_before_public_spawn` (currently2129): fake successful launch sets neither active map nor flag and expects two same-turn calls even on SQLite. It bypasses the real contract already. Replace/consolidate into the real internal-priority batch witness, with SQLite progression across later turns; exact order remains internal before public. Do not merely patch expected counts to new constants.
- C1-only `test_prior_launch_receipt_allows_one_new_authorization` and `test_receipt_continuation_preserves_internal_priority`: remove with C1's rejected exception.
- C1-added STOP/checkpoint/replay keepers: do not carry initial-phase progress premises into B. Retire C1-specific versions on restoration; reuse only the existing baseline direct-checkpoint, early/late STOP and custody proofs. If a replay keeper is retained for C2, establish no same-turn submitted launch and assert no extra opening; hold the later successor receipt where its next-turn oracle expects public to remain.

**Inspected and remain valid without invented receipt holds:**

- `test_dispatch_reuses_outer_child_cleanup`: only one input ID exists, so its one-launch oracle is finite-input, not turn-budget. Extend its scope exception sibling for the two-counter record lifetime only if needed.
- `test_spawn_delivery_yields_for_ready_child_maintenance`: one new request, real old-child settlement; extend it to stage evidence in pass2, rather than add duplicated safety-helper tests.
- `test_classification_continuation_keeps_first_dispatch`:65 input IDs but classification_pending intentionally true; selected C2 vetoes only extra passes, so exact first authorization remains valid. Its existing open-gate assertions remain useful.
- `test_startup_window_overlaps_but_bounds_unobserved_processes`: uses at-least receipt predicates and actual startup exposure boundary, not an exact turn count; retain unchanged.
- `test_startup_handoff_reactivates_pending_public_queue`, positive snapshot wake, and terminal retry repair: only one pending successor. Preserve actual custody/notification oracles.
- `test_startup_pressure_waits_without_hot_loop_and_reopens_on_fresh_usage`: third request is published only after owned pressure is installed, so no early second-pass reservation can invalidate its setup.
- `test_manager_reactor_answers_ping_while_child_launch_is_active` is consolidated into `test_manager_services_control_while_real_launch_receipt_is_held[PING]`: real held launch, actual matching PONG, exact successor nonreservation, release and later progress replace the fake-worker keeper.
- `test_manager_launch_worker_success_commits_once_on_main_thread`, failed-launch retry, disabled admission, advisory ownership, pending-precheck, failed-clear residue: one request or genuine admission closure, so no exact second eligible request premise.
- `test_manager_admission_retains_public_while_internal_can_launch`: two sources but public pressure gate is intentionally closed; preserve that admission oracle, do not relabel its fake success as real receipt proof.
- `test_manager_admission_retains_both_lanes_at_internal_limit`, inactive/public no-pending tests, custom-inbox exclusion: no authorized first launch, so no extra pass.
- Service-convergence ordering/self-write tests: order/eventual-presence assertions rather than exact real launch counts; keep. `test_completion_to_next_real_launch_does_not_scan_sibling_backlog` uses a bounded backlog-complexity oracle, not exactly one launch; keep its measured ceiling, no blanket loosen.
- Lifecycle/drain/signal/idle/autostart tests without multiple eligible preloaded requests remain policy-bound; the full existing suite still runs. No assertion should be weakened merely because C2 adds a pass.

## Smaller firing test set

1. Real2PG/1SQLite batch, internal priority, exact third remainder and later progress; actual submission/receipt order and max live creation1. Budget-loop3 mutant must authorize third. Use two older public rows followed by two internal rows; removing source-hint retention must violate exact internal-first order in pass2. Use existing priority test owner instead of another duplicate.
2. Real held-worker/PING, failed seed and synchronous start/checkpoint variants; exact successor nonreservation and later progress. Charge-after-seed and submitted-onFalse mutants fire. No new result-counter test.
3. Boundary STOP with internal successor and exact reservation history;33 initial PINGs proving first-pass no-starvation/no-release;33 boundary PINGs proving no extra pass until the drain truly empties. Delete-boundary-drain / returnTrue-on-cap mutants fire. Existing per-call stalled-control tests retained.
4. Internal128 aggregate: publish128 malformed internal rows, then valid V, with eligible public P known active. Verify real `_drain_priority_queues` ran, V remains pending at quota exhaustion, and P was never reserved; next turn V must precede P. Removing the exhausted-internal priority guard must reserve P first and fail. Companion cases use exactly128 malformed rows then actual empty (P may proceed), and a narrowly scoped real queue pending-read failure only at the exhausted-quota guard (P must remain untouched); changing unknown to False must fail. Do not mock the helper result. Public one-poison-REQUEUE witness across watcher+explicit routes, actual128 exact reservations and source retained; remove poison via exact delete then valid successor progresses. Omit-lane-cap / reset-per-helper mutations produce actual excess. No artificial budget or fake validation.
5. Fresh pressure between actual receipt1 and extra selection2, described below; cache-first-admission mutant fails exact nonreservation. B's per-delivery maintenance staged in pass2; clearing cleanup scope between passes must allow a forbidden second reservation. No extra helper-veto mutant that surviving B gate masks.
6. Extend the existing65-row classification-continuation keeper with the real receipt-publication barrier below: first submission settles in the same pass, classification remains pending, all other extra-pass gates are open, and no second authorization occurs. Deleting only the extra classification veto must authorize a second ID and fail. Add pending SIGTERM at the same settled-receipt boundary via actual `manager.note_termination_signal(signal.SIGTERM)`; assert its pending request is true while stop/draining remain false, and the successor is never reserved. Removing only `_has_pending_termination_request()` from the extra boundary must permit the forbidden reservation. This exercises the production signal-recording path without sending SIGTERM to pytest; a later outer turn processes the recorded signal normally. No-spin/scope and original custody/recovery suite retained. No tests solely for removed aggregate-result/control or service-readiness machinery.

## Deterministic receipt publication for extra-pass tests

Batch, classification, pending-SIGTERM, pass2 maintenance and pressure witnesses must establish the same interleaving rather than depend on thread speed. Forward the real `_publish_worker_result` and set a test Event only after the original publisher successfully enqueues the matching actual child-launch result/error. A forwarding real `_launch_child_task` wrapper, after it returns True, waits boundedly for that publication Event before returning True to its caller. Do not consume or fabricate the receipt: inherited post-delivery result handling must apply it. A publication that wins the race is retained by Event. For the batch bound mutant, apply this barrier to all four named eligible IDs, not just the first two. Explicit fallback still lacks a following drain and cannot qualify. Every barrier is bounded; teardown releases all holds and joins actual workers before child/process cleanup and patch restoration. Assert both publication and main-thread application for the intended first-pass ID, with one actual outer-turn enclosure.

For pending signal and classification witnesses, stage intent/continuation after actual receipt application but before the extra boundary and independently assert all other guards are open. For per-delivery maintenance, make the real sentinel/deadline observation ready at that same boundary; do not introduce a fake broad maintenance predicate. Extend stalled-control coverage to observe real row removal followed by an empty drain and assert the stalled retry deadline is cleared.

## Exact pressure interleaving and trace proof

Use two real public requests with normal classification and room. Observe first receipt through a forwarding `_handle_child_launch_result` wrapper and require it is applied within the same owned `process_once` as submission1. The next `_admission_allows_queue(public)` invocation after that receipt is extra selection2. A test-local phase tag around that **real** call identifies its usage read; do not select the globally second usage read (startup checks can read too).

At the tagged actual admission-call boundary, before forwarding the real admission owner, use the existing ready-child barrier and command the independent held-pressure actor to open the required real population. Independently observe low pre-pressure usage, wait for its bounded acknowledgement, and observe installed usage in72<=U<80. Enqueue the late internal request after priority selection at this boundary. The forwarding `_read_postgres_connection_usage` wrapper only records the candidate's actual fresh read. After the turn, assert the readiness, headroom, acknowledgement and installed-pressure premises before exact historical nonreservation; then assert the candidate read the actual pressured population. This keeps real pressure present even when a mutant skips the read, so that mutation fails on forbidden custody rather than missing setup. The actor remains held through assertion; release and drive later progress. This is a test barrier on the owner thread, not product waiting. Do not mock the admission outcome. Keep measurement server capacity300 and default12 workers unchanged. This focused correctness fixture may reuse `tests/system/test_measured_admission_pg.py::test_startup_pressure_waits_without_hot_loop_and_reopens_on_fresh_usage`'s configured ceiling80/reserve0.1 (public boundary72) on that server, with enough real actor connections to cross72; assert the measured total rather than an assumed connection count. No physical-server saturation is needed. A direct wrapper around the identified admission call may set the tag without adding a reusable scenario framework.

The measurement harness already wraps `_timed_manager_call` and emits existing phase timing with unique span IDs (`test_manager_dispatch_costs.py` currently405–426). `queue_dispatch` spans completed twice within one actual turn enclosure establish an opened extra full pass, even if no second authorization succeeded. Count unique spans per manager/turn, excluding incomplete enclosures. Actual two authorization IDs in a turn establish used opportunity; do not infer an opened-but-denied pass from them. If no second span/authorization is observed, mark the batching-opportunity proof unavailable rather than add a hidden production counter or claim the optimization was exercised. Quiet elapsed remains the retention oracle.

## Proposed Spec Delta

Promotion strategy A: the retained B specifications remain normative. The following exact B-based replacements are proposed for C2 and must pass the current plan review before promotion. Promote behavior text before runtime edits; update mappings and lesson with implementation. C1 wording has been removed. B cleanup reuse remains intact.

### `docs/specifications/05-Message_Flow_and_State.md` (before_runtime)

Replace exactly:

```text
While a launch worker is active, the manager must
continue to answer task-local control messages such as `PING`, but it must not
accept another spawn message in the same turn.
```

With:

```text
While the single launch-worker lane has an active request, the Manager
continues task-local control and lifecycle processing but does not reserve
another spawn request. PostgreSQL permits at most two attempted launch
authorizations in a turn; SQLite permits one. An attempt closes its delivery
pass's allowance before inbox seeding, including when seed, checkpoint or
worker submission subsequently fails. A receipt applied at turn start still
consumes that turn's ordinary allowance. Only a successful new worker submission
in an ordinary delivery pass, followed by receipt settlement before that complete
pass returns, can qualify one additional PostgreSQL delivery pass. That boundary
must drain controls to observed emptiness, remain free of stop, draining and
termination intent, and yield any remaining classification continuation. The
additional pass uses the same source priority, startup, custody and fresh
admission gates, with the existing child-maintenance gate before reservation.
A failed worker result can qualify after successful submission and receipt settlement; all retry and custody gates still apply. The indexed frontier is reused without another classification page; later unclassified arrivals await normal classification, while known indexed work retains family ordering. An explicit public fallback submission without a following receipt drain cannot qualify.
The Manager never waits for a receipt to fill a batch. STOP observed at the final
pre-seed control fence prevents authorization; already-authorized work retains
custody through seed, checkpoint, worker handoff and settlement. No post-seed STOP
requeue/reseed path is introduced.
```

### `docs/specifications/03-Manager_Architecture.md` (before_runtime)

Replace exactly:

```text
The next reactor
turn reevaluates known work through normal measured admission and custody gates
without waiting for inactive-source rediscovery.
```

With:

```text
Known work is reevaluated through normal measured admission and custody gates
without waiting for inactive-source rediscovery. This normally occurs on the next
reactor turn. The bounded additional PostgreSQL pass in [MF-6] may reevaluate work
only after a successful new submission settles within the preceding ordinary
pass and the boundary control drain reaches empty. A prior receipt applied at
turn start does not qualify that exception.
```

### `docs/specifications/03-Manager_Architecture.md` (before_runtime)

Replace exactly:

```text
Manager queue delivery may reuse child cleanup already performed at that turn's
maintenance boundary. The work handler omits its duplicate sweep only within an
explicit reactor-owned scope covering ordinary queue delivery; standalone handler
calls, service convergence and shutdown retain their cleanup. The scope is cleared
on failure. Before each spawn delivery in the scope, including the first, a ready
child-sentinel observation or a due child sentinel/post-exit maintenance deadline
yields to immediate normal settlement. Child-cleanup lifecycle commits do not run
inside this ordinary delivery scope. Reusing this child cleanup does not change
the normal phases or deadlines of other maintenance owners or make a serviced
classification-page continuation a veto on the first eligible dispatch.
```

With:

```text
Manager queue delivery may reuse child cleanup already performed at that turn's
maintenance boundary. The work handler omits its duplicate sweep only within an
explicit reactor-owned scope covering ordinary queue delivery; standalone handler
calls, service convergence and shutdown retain their cleanup. The scope is cleared
on failure. Before each spawn delivery in the scope, including the first, a ready
child-sentinel observation or a due child sentinel/post-exit maintenance deadline
yields to immediate normal settlement. Child-cleanup lifecycle commits do not run
inside this ordinary delivery scope. Reusing this child cleanup does not change
the normal phases or deadlines of other maintenance owners or make a serviced
classification-page continuation a veto on the first eligible dispatch.

Ordinary dispatch uses at most two complete delivery passes on PostgreSQL and
one on SQLite. Each pass permits at most one attempted authorization and closes
its allowance before seeding, including on failure. Only successful submission
and receipt settlement within the preceding pass may open the one additional
PostgreSQL pass, after a bounded control drain proves empty and stop, draining,
termination and classification-continuation checks permit it. The complete pass
retains BaseTask's existing pre- and post-delivery result drains; no extra result
drain or wait is added to obtain a receipt. The inherited queue-delivery call
returns before its post-delivery receipt handling; that handling may qualify the
next complete pass.

Separate Manager-owned public and internal attempted-fetch allowances each cap
work at 128 per outer turn, across inherited watcher and explicit drain paths.
They include unsuccessful fetches and malformed or rejected requests, including
Manager requeue dispositions, and reset only at outer-turn entry. Existing
per-call result and control limits remain; no aggregate outer-turn result or
control quota is imposed. When the internal allowance is exhausted, pending or
unknown internal work prevents public reservation; positively observed empty
permits normal public gates. The additional pass reuses the classified frontier
without refilling classification; later unclassified arrivals await normal
classification, while known indexed work retains [MA-1.8] ordering. A failed
worker result may qualify after successful submission and receipt settlement.
An explicit public fallback without a subsequent result drain cannot qualify.
First-pass control handling remains unchanged; the
observed-empty requirement applies only before an additional pass.

The existing child-maintenance gate remains active before every reservation in
both passes. Other maintenance owners retain their normal phases and clocks;
the tail runs after at most one additional bounded pass. Extra passes do not add
publication observation or a second maintenance scheduler. Exact receipt
reconciliation, durable custody, the startup exposure window and fresh
per-request admission remain independent gates. Source hints deferred only by
a successful submission remain available to the next full priority pass. On
leaving an opened extra pass its allowance closes even if it made no attempt,
so tail convergence cannot attempt another authorization after an opened extra
pass. Existing event sources
provide continuation; unchanged blocked backlog gains no new wake.
```

### `docs/specifications/05-Message_Flow_and_State.md` [MF-6] (before_runtime)

Replace exactly:

```text
- manager-owned internal spawn dispatch uses the same launch path and remains
  strict-priority over ordinary work
```

With:

```text
- manager-owned internal spawn dispatch uses the same launch path and remains
  strict-priority over ordinary work. When its attempted-fetch allowance is
  exhausted, pending or unknown internal work defers ordinary reservation until
  the next turn; positively observed empty permits the normal ordinary gates.
```

### `docs/specifications/03-Manager_Architecture.md` [MA-3] (with_runtime)

Replace exactly:

```text
_Implementation mapping_: `weft/core/manager.py::Manager._process_manager_reactor_turn`
owns the ordinary delivery scope and clears it with `try/finally`;
`Manager._process_queue_message` checks the existing child-sentinel observation
and `Manager._manager_child_timeouts` before reservation;
`Manager._handle_work_message` retains cleanup outside that scope.
```

With:

```text
_Implementation mapping_: `weft/core/manager.py::Manager._process_reactor_turn`
owns the outer-turn dispatch budget and clears it with `try/finally`;
`Manager._process_manager_reactor_turn` owns the ordinary delivery scope,
bounded passes, extra-pass boundary and unconditional allowance reclosure.
`Manager._spawn_dispatch_allowance_open` owns attempted-fetch limits,
exhausted-internal priority and deferred source-hint retention;
`Manager._process_queue_message` applies that allowance, the existing
child-sentinel/deadline gate and fresh admission before charging each fetch.
`Manager._extra_dispatch_pass_ready` checks settlement and lifecycle evidence;
`Manager._process_manager_delivery_pass` reuses inherited delivery and public fallback.
`Manager._internal_spawn_pending` supplies the existing queue observation with
fail-closed unknown handling at the quota boundary; existing convergence uses
its default policy. `Manager._drain_control_queue_first` supplies bounded
observed emptiness. `Manager._handle_work_message` retains cleanup outside the
scope; `Manager._launch_child_task` closes allowance before seed and records
actual successful worker submission.
```

### `docs/specifications/07-System_Invariants.md` (with_runtime)

Replace exactly:

```text
For [MANAGER.18], `weft/core/admission.py::_startup_window_allows_launch`
owns the bounded overlap predicate. `Manager._startup_dispatch_blocked` and
`Manager._resume_startup_dispatch` in `weft/core/manager.py` own negative
readiness and next-turn reactivation, separately from resource drain-wait policy.
```

With:

```text
For [MANAGER.18], `weft/core/admission.py::_startup_window_allows_launch`
owns the bounded overlap predicate. `Manager._startup_dispatch_blocked` and
`Manager._resume_startup_dispatch` in `weft/core/manager.py` own negative
readiness and source reactivation, separately from resource drain-wait policy.
`Manager._process_manager_reactor_turn` owns the bounded serial-pass exception
in [MF-6]: only a new submission settled within an ordinary pass may qualify
one additional PostgreSQL pass after controls reach empty. Initial receipts
retain next-turn continuation. `Manager._process_queue_message` preserves
per-delivery startup, custody, admission and child-maintenance gates and the
aggregate source-attempt bounds.
```

### `docs/lessons.md` (with_runtime)

Replace exactly:

```text
- A worker result committed at the start of a manager turn still counts as
  launch work for that turn. Do not accept another spawn request before control
  has had a chance to run; otherwise a fast child-launch worker can recreate a
  same-turn STOP race.
```

With:

```text
- A worker result committed at the start of a manager turn still counts as
  launch work for that turn. Revised 2026-10-28: bounded serial batching may
  reopen one PostgreSQL delivery pass only after a new submission in the prior
  ordinary pass has settled and a bounded control drain proves empty. Close each
  pass before seeding, including failed attempts; never wait for a receipt to
  fill the batch. This preserves the control-order reason for the original rule:
  a fast launch worker must not bypass pending STOP. Initial receipts do not
  qualify the extra-pass exception.
```

## Dependency-Ordered Slices

Before the first implementation edit, record answers to these comprehension checks: (1) Can a free lane authorize work? Expected: no, resource/startup/ownership/custody and control gates remain. (2) Can a serial batch wait for a receipt? Expected: no, return to the arbiter; a small or empty batch is valid. (3) Can missing execution evidence justify reseeding? Expected: no. (4) Does suppressing the nested sweep remove settlement? Expected: no, ready evidence yields to the normal maintenance owner. Wrong answers require re-reading.

1. **Establish a reproducible baseline and cost witnesses.** Read `weft/core/manager.py`, `weft/core/tasks/base.py`, `weft/core/tasks/multiqueue_watcher.py`, `weft/core/tasks/service.py`, `weft/core/launcher.py`, and the current family system test. Extend `tests/core/test_manager_burst_bookkeeping.py` for operation counts and add `tests/system/test_manager_dispatch_costs.py` (new) for the real fixed workload/trace fixture if it cannot fit clearly in the existing file. Reuse the lifecycle pattern of `tests/system/test_family_scheduling.py::family_harness` without changing its existing six/eight-input workload or calling a fixture function directly; use a test-local real harness fixture for the fixed 12-parent diagnostic. Reuse workload functions in `tests/helpers/family_workload.py`, and `WeftTestHarness`. Record A and the predeclared comparison metrics, baseline spread, and repeatability criterion before B. Keep process/broker paths real. Stop if the baseline cannot be reproduced or measurement changes scheduling authority. Measurement tests need no invented failing performance assertion.
2. **Isolate duplicate-sweep removal.** Promote B and record baseline; edit `weft/core/manager.py` and `tests/core/test_manager_burst_bookkeeping.py`, with lifecycle assertions in `tests/core/test_manager.py` / `tests/core/test_admission_recovery.py`. Write RED for redundant sweep count and scope cleanup, plus real exit/terminal-retry behavior. Implement B only; retain one new child launch per turn and receipt-consumes-turn. Run A/B comparison. Stop if standalone cleanup disappears, scope survives failure, or known exit/custody evidence is bypassed. Independent review before the next behavior slice.
3. **C1 completed; review and measure B+C2.** C1 failed its retention gate and is removed. Review the selected fixed two/one-pass design and exact proposed spec replacements above, then promote accepted behavior text. Write the named REDs and qualify keepers before runtime changes. Implement only Manager-local pass structure, two lane-attempt counters and successful-submission state; preserve existing result/control limits and child-maintenance gate. Add the internal pass constant to `_constants.py`; no generic BaseTask or watcher API change. Run GREEN, declared mutations and fresh-eyes/cross-family completed-work review, then fresh B/C2/B quiet and trace batches. Keep C2 only on the unchanged PostgreSQL elapsed benefit and both-backend primary nonregression gates. Otherwise restore B and record rejection. Stop on waiting for receipts, reentrant lifecycle commits, hidden concurrency, changed custody, or unresolved fairness defects.
4. **Decide on concurrency from the serial result.** Use the explicit D gate above. Inspect `launch_task_process` and supported multiprocessing resource-tracker/`set_executable` behavior only if overlap is actually justified. Record keep-serial or a separately reviewed successor scope. This step does not authorize implementing slot maps or multiple workers. A no-go closes that branch without weakening the serial outcome.
5. **Reconcile and close the implemented serial scope.** Update only promoted specs, their implementation mappings, code docstrings, relevant lesson, this plan's evidence/deviations, and `docs/plans/README.md`. Run final backend/runtime checks and independent completed-work review. Close this plan only after the chosen serial implementation and proofs land; plan-authoring completion alone leaves status draft. If concurrency is warranted, identify its indexed successor without claiming that implementation is complete here.

## Acceptance Matrix

Use real queues, exact moves, real process creation and `WeftTestHarness`. For global PostgreSQL pressure use `tests/helpers/pytest_pg_runtime.py::isolated_postgres_server` before harness/target construction; schema isolation does not isolate connection usage. Narrow publication fault injection and barriers around actual process creation/event delivery are allowed. Do not replace the launch pipeline, admission controller, or broker. Barriers prove ordering; machine-specific timing gains are diagnostic measurements.

| Boundary | Observable proof and regression control |
|---|---|
| B scope | A real turn dispatches work after one outer cleanup, with no nested handler sweep; standalone handler and shutdown retain cleanup. Raise inside delivery and prove the scope is cleared. Reinstating the duplicate or permanently suppressing cleanup fails opposite assertions. |
| Ready maintenance | Deliver a real child exit or make a real terminal retry due after the outer sweep but before the turn's next spawn delivery, including the first. Normal settlement precedes next launch. Use a module-owned clock only for the due-time boundary, never shared stdlib mutation. Suppressing the yield must fail. |
| C1 continuation (historical, rejected) | Completed real receipt/control/custody proofs are retained in the review ledger. Candidate-specific active tests and initial-phase exception were removed after failed retention. |
| C2 batch bound | With already-ready real receipts at post-pass boundaries, record at most the selected number of authorizations and eventual continuation for excess work. Holding the real worker before completion proves the second request stays unreserved and PING is answered. Both removing the bound and waiting/overlapping workers must fail their respective witnesses. |
| Fresh admission and custody | Change real pressure between serial authorizations; the next request stays in source. Unknown usage denies. Fail pre-launch checkpoint: no worker; fail post-launch checkpoint: no next ordinary launch. Restore evidence and prove wake/progress. Cached positive admission or skipped checkpoint gating must fail. |
| STOP and seed | Queue STOP before selection, before receipt handling, and during seed/checkpoint. Pre-seed STOP returns only positively unlaunched exact work; post-authorization STOP does not requeue/reseed it. Verify a child side effect occurs once, including successor handling of remaining work. |
| Selection and internal priority | Check FIFO independent families, round-robin paths, LIFO descent and oldest-sibling sharing using real reservation order, not receipt timestamps. At each free lane an eligible internal request takes its existing priority. No new provisional lineage. |
| Budgets and fairness | Fixed two/one passes plus the pre-seed allowance fence bound attempted authorizations. Two aggregate lane-fetch counters cover watcher and explicit paths. Internal129 invalid rows with a real eligible public source exercise priority draining; a public poison REQUEUE row exercises actual same-ID refill. Literal128 attempts and exact remainder/progress must fail removed-cap mutations. Existing per-call result/control limits remain. Initial33 PINGs preserve first-pass progress; boundary33 PINGs defer only the extra pass until observed empty. |
| Classification continuation | Put more than one classification page in source (the reviewer reproduced the state with 65 valid rows). After one real bounded page, a known eligible candidate must still receive its first dispatch opportunity even while `_family_classification_pending` is true and a continuation timeout is zero. Preserve immediate continuation; yield before extra C2 passes for that page work. Converting every pending/zero-timeout condition into a pre-dispatch veto must fail. Newly ready exits and due terminal retries still force normal settlement. |
| Wake and SQLite | Blocked backlog causes no unchanged-work spin; completion/publication wakes normal dispatch without a new timer. Final-pass receipts retain a continuation wake. SQLite still permits one authorization per turn and its existing startup bound. |
| Recovery and cleanup | Preserve existing exact custody crash-point, missing-result, late-result self-reap and STOP deadline tests. B/C do not alter the checkpoint shape or add concurrent obligations. Existing detached-task survival remains. |

Retain the control/custody assertions in the real held-worker PING/STOP replacement and `test_manager_launch_worker_success_commits_once_on_main_thread`, `test_manager_stop_mid_handler_requeues_reserved_work_unlaunched`, `test_manager_late_child_launch_self_reaps_after_cleanup_deadline`, and `test_unknown_launch_custody_is_durable_and_cannot_requeue_or_spin`; update only obsolete receipt-turn expectations.

## Verification

Load `. ./.envrc`. Plan authoring gates (no need to rerun runtime suites for prose):

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py
./.venv/bin/python bin/check-doc-paths
./.venv/bin/python bin/coalesce-check
git diff --check
```

Directly verify plan paths and symbols too: the doc-path checker excludes plans. During implementation use configured worker counts:

```bash
./.venv/bin/python -m pytest tests/core/test_manager.py tests/core/test_admission_recovery.py tests/core/test_manager_burst_bookkeeping.py
bin/pytest-pg tests/core/test_manager.py tests/core/test_admission_recovery.py tests/core/test_manager_burst_bookkeeping.py
./.venv/bin/python -m pytest -m '' tests/system/test_family_scheduling.py tests/system/test_manager_dispatch_costs.py
bin/pytest-pg --all tests/system/test_family_scheduling.py tests/system/test_manager_dispatch_costs.py
./.venv/bin/ruff check .
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/ruff format --check .
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
```

The new system test path becomes runnable in Slice 1. Final implementation gates follow `docs/specifications/08-Testing_Strategy.md` [TS-4] and the actual platform-specific recipes in `.github/workflows/test.yml`, including normal-load PostgreSQL/SQLite, slow and relevant extension suites; retain the existing separate extension/integration scheduling rather than combining roots into a different load; targeted tests alone do not close the runtime plan. Record RED assertions, restored GREEN, mutation sensitivity, raw timing comparisons, and review dispositions. At final reconciliation, verify reciprocal code/spec ownership and plan backlinks rather than inventing an unavailable traceability tool.

## Rollout, Rollback, and Deferred Concurrency

No deployment is part of this work. Runtime rollout follows independently verified slices into isolated contexts, then separately authorized Manager turnover. Queue names, checkpoint formats, admission policy, startup exposure, single-worker transport and family ordering are unchanged by B/C. Rollback reverts the selected code/spec slice and uses normal Manager turnover; preserve reserved work and checkpoints, do not replay unknown execution or kill detached tasks to tidy the rollback. There is no durable migration or intended one-way door.

If D passes later, the former design's real obligations must be rederived and reviewed: exact group/request identity; unresolved requests retaining their slot; per-request stale-worker proof; selection-order lineage before another selection; successor ambiguity without local ownership exceptions; STOP during seeding; one shared destructive cleanup deadline; non-destructive drain waiting for actual producer closure before final result drain; and supported concurrent multiprocessing behavior. Preserve the historical review below as evidence of those hazards, not ready-made permission to add them. Do not promote its former [MA-1.8]/[MANAGER.18] custody exception during serial work.

Residual limits: neither ordering nor batching frees connections held by waiting parents; multiple Managers do not provide globally atomic admission; external pressure may require the existing timed recheck; custody ambiguity may need operator resolution. These are accepted existing limits, not reasons to weaken the gates.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|

The four before-runtime behavior replacements were promoted on 2026-10-28 after round3 PASS against a385f3e8. The candidate runtime and with-runtime mappings are now implemented; execution, review closure and measurement retention remain pending. B is retained and C1 has been removed.

## October 27 Revision and Review Record

Owner direction: measure bounded dispatch batching and duplicate-sweep removal before adding concurrent launch workers. Replaced the former concurrency-first operative design and deltas; preserved the earlier review record below. Author family: OpenAI. Author fresh-eyes review, failed Tier-1 attempts, and completed Tier-2 review are recorded here. The fallback is not cross-family approval.

Fresh-eyes finding A1: “batch first” could silently mean multiple active workers. Corrected with A/B/C1/C2/D arms, one lane throughout B/C, ready-only post-pass receipts, and an explicit no-wait/no-benefit outcome. A2: broad turn time is not removable overhead. Corrected with nested-span warning, independent operation counts, unchanged safety work and comparisons. A3: repeated BaseTask passes could reset drain budgets. Corrected with shared outer-turn work/result budgets and a falsifiable fairness row. A4: the old custody exception would prematurely permit concurrency. Removed it from the operative delta and explicitly prohibited its promotion during B/C. Residual risk: C2 may offer too little natural receipt readiness to justify its control-flow complexity; the plan permits rejecting it.

Plan authoring and independent Tier-2 review: complete, with accepted findings corrected and focused re-review PASS. Implementation has not begun; baseline measurements and conditional spec promotion remain execution gates.


### Current reviewer availability and fallback record

2026-10-27: author family OpenAI. Tier-1 Anthropic Claude Code 2.1.293 passed the 120-second liveness probe (`PROBE-OK`) and a write-attempt containment probe (no write tool, probe file absent). Review invocation: `claude -p <embedded-plan-brief> --permission-mode plan --restricted --strict-mcp-config --tools Read,Grep,Glob --allowedTools Read,Grep,Glob --no-session-persistence --effort high`, closed stdin, Python subprocess timeout 540 seconds. Both the admission and repair plan calls reached 540 seconds with empty stdout/stderr and no verdict; neither counts as review approval. Exact briefs, hashes and result records: `/tmp/weft-plan-review-20261027/admission-round1.*` and `repairs-round1.*`.

Second Tier-1 family attempt: Grok 1.0.62, `grok --cwd /Users/van/Developer/weft --sandbox read-only --always-approve --disable-web-search --output-format json --prompt-file /tmp/weft-plan-review-20261027/grok-live.prompt`, closed stdin, 120-second bound. It exited 1 before model startup: “sandbox could not be applied: socket deny resolution failed: could not resolve runtime-socket deny path /var/run/docker.sock: endpoint is a symlink to a path outside the deny list”. It refused to start without protections. No unenforced retry was made. This availability failure applies to both plan reviews.

Tier-2 review is now selected: **same-family fallback (degraded)** via a separate native OpenAI reviewer for each plan, after the logged Tier-1 failures. This is not cross-family approval. CLI timeout diagnosis continues separately; it does not replace review or authorize changes to tool security settings.


Timeout diagnosis: a subsequent bounded Claude call with identical containment and high effort, plus stream-json/verbose/partial-message/debug telemetry, completed a real specification Read in 8.806 seconds (exit 0; model reported as claude-opus-5-5). This rules out a persistent startup/authentication/Read-tool failure at diagnosis time, but does not identify the earlier full-review timeout cause. Those failures remain unclassified timeouts, not proven model stalls or version regressions. Proposed invocation maintenance: retain the security posture and bounded deadline, stream progress and require a final successful result with the requested verdict; progress alone is not approval. No skill or security-setting change was made. Diagnostic artifacts: `/tmp/weft-plan-review-20261027/claude-tool-diagnostic.*`.

### Tier-2 review, round 1 and dispositions

Reviewer: native OpenAI agent `/root/scheduler_evidence`, separate from the plan author; **same-family fallback (degraded)**. Unit: the October 27 operative serial-first revision against source baseline `e220748f`, excluding the superseded October 22 design. Verdict: **BLOCKED as written on S1/S2**. Findings below are the reviewer's failure assertions; no implementation is claimed.

S1 [P2], verbatim:

> There is no existing aggregate watcher work budget to share. `_drain_priority_queues` repeatedly drains the highest non-PEEK priority until it stops making progress. The `max_messages` bound belongs only to `Manager._drain_spawn_requests_from_queue`, not the preceding BaseTask watcher pass. A real isolated Manager probe with 129 malformed internal requests and one pending control row reserved all 129 during one `_drain_queue()` call, despite `MANAGER_INTERNAL_SPAWN_DRAIN_MAX_MESSAGES == 128`. No child launched.

S2 [P2], verbatim:

> “Another due owning maintenance obligation” needs a distinction between work already serviced this turn and newly due/unserviced work. `_classify_family_requests` deliberately retains `_family_classification_pending` after a full bounded page; `_admission_retry_timeouts` then returns zero. A real probe with 65 valid pending requests produced `pending_continuation=True`, `eligible_candidate_exists=True`, and timeouts `[~1.0,0.0]` after one classification pass. Treating that zero as a pre-delivery veto forces full backlog discovery before launching already-known eligible work. Continual arrivals can make the veto persistent.

| Finding | Disposition | Operative correction and proof |
|---|---|---|
| S1 | Accept within C2 | Add Manager-owned attempted spawn-delivery counters before fetch/reservation across inherited and explicit paths, with existing lane ceilings and outer-turn reset. Count rejected/malformed attempts; preserve fresh-turn continuation. Revised design, proposed MA-3 delta, Slice 3 and acceptance matrix require the 129-row priority-topology regression. No generic watcher rewrite. |
| S2 | Accept within B/C | Distinguish a bounded page already serviced at turn start from newly due/unserviced safety maintenance. Preserve first dispatch of known eligible work while classification continuation remains; remaining page work may end extra C2 passes. Revised design/delta and 65-row acceptance control forbid the whole-backlog veto. |
| Result/control accounting caution | Accept | Aggregate Manager calls as well as BaseTask pre/post-pass calls. Skip a result-drain call at zero remaining budget because `max_results=0` still drains one; do not reset through helpers. |

Focused re-review, 2026-10-27, same reviewer and degraded posture: **PASS**. Verdict, verbatim: “Both accepted findings are resolved in the operative plan.” The reviewer verified pre-fetch counters across watcher/explicit drains, outer-turn reset and continuation, first eligible dispatch after bounded classification, and the corresponding spec delta and regression rows. It also verified the zero-result-budget skip and inclusion of Manager calls outside BaseTask. Closing assessment, verbatim: “No new contradiction or remaining blocker found within this focused review. No edits or additional runtime tests were needed; the relevant runtime source is unchanged from the prior verified probes.” No standing constraint or refused alternative was proposed for reversal.

Plan-authoring verification, 2026-10-27: metadata/spec-hygiene tests passed (6 tests, 0.98 s); `bin/check-doc-paths`, `bin/coalesce-check` and `git diff --check` passed. Coalescing evidence: 13 SHA claims (6 foreign), 208 retrieval cues (206 retired-plan cues), all local cues resolve. Plan paths/symbols were also inspected directly because the doc-path checker excludes plans. Only plan/index text and related-plan backlinks changed; no runtime/test implementation or normative spec promotion occurred.

## Historical October 22 Review Record (superseded sequence)

The following is the preserved review record of the earlier concurrent-worker-first draft. Its PASS statements apply to that version only. Instructions, spec deltas, and implementation sequence above govern the revised plan; historical suggestions below cannot bypass the new D gate.


Author fresh-eyes review, 2026-10-22: accepted S1 (P1), the duplicated receipt-before-next-dispatch clause conflicted with concurrency; added exact replacements in [MA-1.8] and [MANAGER.18], explicitly preserving successor ambiguity. Accepted S2 (P2), sentinel draining alone does not settle/reap; initially proposed exact-TID cleanup; superseded after F8/F10 by event-ready slice yield and normal next-turn cleanup, avoiding a new API. Accepted S5 (P1), freeing dead-worker slots could cycle ambiguous internal launches past the exposure bound; unresolved requests now retain slots and the internal allowance until positive resolution. Accepted S4 (P3), identifier check corrected `_drain_spawn_requests_from_queue` and named the actual family fixture rather than an assumed burst fixture. Accepted S3 (P3), group completion inspection was underspecified; named the existing snapshot and completion-before-publication ordering. Residual risk: extra crash-window exposure up to the existing six-startup bound; benchmark benefit is unproven. Cross-family plan review round 1: BLOCKED; dispositions below. Round-2 verification: FAIL on R2-1/R2-2, both accepted and corrected below; round-3 focused verification PASS; A1 check-order clarification accepted and applied. Plan authoring/review complete; implementation remains gated by Slice 1 measurement and the proposed spec-promotion step. Implementation review and runtime acceptance: not started.

Reviewer inventory, 2026-10-22: Claude CLI liveness returned `PROBE-OK`; plan-mode write-attempt probe did not create the requested repository file (it wrote only an external Claude plan). Review posture: `--permission-mode plan --allowedTools Read,Grep,Glob`, as prescribed by `skills/call-agent/SKILL.md`. Repository state checked before review. Native research assistance is not counted as independent review.


### Cross-family round 1: verbatim findings

<details>
<summary>Claude review, 2026-10-22</summary>

**Verdict: BLOCKED as written.** One finding (F1) makes the plan unsafe to implement as written: its new STOP fence would deliver the same work to a task twice. Once F1–F4 are addressed it can be implemented confidently without making the Manager less robust, and F5 makes it simpler.

**Premise check:** the plan's account of the current code is correct:
- **Single-flight gates:** the per-turn flag is checked in three places (`manager.py:5172`, `8390/8399`, `8568`), and a receipt sets it (`1838`).
- **Error routing:** errors go to the first active request via `next(iter(...))` (`1937/1951`).
- **Stale detection and leadership:** stale detection uses global worker activity, and leadership is skipped while a launch is active (`4216`).
- **Family registration timing:** a request leaves the pending set at reservation (`5780`) but is registered as launched only at commit (`1700`).
- **History:** the cited commits match.

## Findings

**F1 [P1] — Final STOP fence before worker handoff (STOP section and MF-6 delta)**
- **Problem:** `_seed_child_inbox` (`2080`) writes the child's inbox message before the checkpoint, and that write isn't idempotent. The plan's return path forces REQUEUE through `_release_unlaunched_spawn_request`. Whoever relaunches it reuses the same TID, so `T{tid}.inbox` is seeded a second time and the task can run its work twice. Today the last STOP check, `_control_allows_child_launch` (`5539`), runs *before* the inbox is seeded.
- **Disposition:** Delete the post-write fence and its "remove the obligation, then return" path. Keep the existing pre-seed check as the last STOP check; once seeding starts, the request is dispatched. Invariant 6 already disclaims atomicity between STOP and process creation. Remove the matching STOP-matrix rows and delta sentence. This also saves one control read per launch.

**F2 [P2] — Shutdown can finish while launches are still in flight**
- **Problem:** Three exits look only at `_child_processes`, never `_active_child_launches`: drain finish (`8685`), superseded shutdown (`4875`) and the no-children yield (`4278`). Cleanup then kills any process created after it starts (`1577`). On supersession, freshly launched user tasks are killed and reported as rejected, contrary to "already-owned work is retained" and ALT-WT-015's detached survival. Concurrency raises the number of affected launches from at most 1 to 6. The plan's risk section doesn't mention this.
- **Disposition:** Make all three exits also wait for in-flight launches, bounded by the existing drain window. Add a supersession-during-burst test.

**F3 [P2] — The leadership fence doesn't exist**
- **Problem:** Removing the early return at `4216` isn't enough. `_has_actionable_leadership_work` (`4123`) returns True whenever launches are active, so the check returns False at `4268`: no fence forms and a busy Manager never yields.
- **Disposition:** On positive lower-primary proof, enter the existing `_begin_leadership_drain`. Setting `_draining` already blocks new launches, and that path already revalidates and resumes. Drop active launches from the veto, and rely on F2 so the drain waits for receipts.

**F4 [P2] — Per-group stale detection can drop a good receipt**
- **Problem:** `_mark_service_worker_thread_completed` (`596`) marks the group inactive *before* publishing its result. A group can look dead while its receipt is still queued, so the launch would be marked unresolved and the real receipt dropped as stale.
- **Disposition:** Keep the "no pending worker results" condition alongside the per-group check, and evaluate it only after the turn-start drain. Add a test where the receipt is still queued when the grace period expires.

**F5 [P2] — The six launch groups are unnecessary**
- **Problem:** Each `_start_service_worker` already creates a fresh thread, so the groups only carry identity. They bring slot maps, slot-reuse rules, stale-event aliasing and the "never started" ambiguity. Six permanently registered groups also look like the "persistent worker pool" Invariant 8 rules out. IMPL.9 already allows `_submit_worker_call` for child launch, and every launch result already carries its request.
- **Disposition:** Use one worker call per request on a lane keyed by TID.
  - The bound becomes the size of `_active_child_launches`.
  - Per-request liveness comes from that thread, with F4's condition.
  - The worker returns an explicit "never started" result if stop or cleanup is already set.
  - If the groups are kept, record why.

**F6 [P2] — The benefit hasn't been measured**
- **Problem:** The plan only reconsiders after all four slices are built. The predecessor run never filled its startup window (it peaked at 3 unfinished startups, below W). That plan deliberately chose to overlap startup intervals, "not worker threads or launches in one reactor turn". The per-launch admission reads and checkpoint writes stay serial either way.
- **Disposition:** Make Slice 1 a go/no-go gate. Split per-launch time into the existing `process_launch` phase versus the reactor-side phases. Set N from the result; two slots may capture all the useful overlap.

**F7 [P2] — Spec delta is incomplete**
- **Problem:** `07-System_Invariants.md` [MANAGER.18] (lines 1139–1160) repeats the MA-1.8 paragraphs word for word. The delta changes only 03, so the two specs would disagree about when lineage registers.
- **Disposition:**
  - Mirror the MA-1.8 insertion into [MANAGER.18].
  - Say "any non-PostgreSQL backend: one in total".
  - List the MA-1.2 implementation mapping (03:552) and the `_resume_startup_dispatch` comment (`1862`) in the delta.

**F8 [P3] — Work between dispatches inside a turn should go**
- **Problem:** Draining receipts, sentinel events and maintenance deadlines *during* the watcher pass risks re-entrancy. Failure handling requeues and re-marks queues mid-pass. Each dispatch already re-checks the correctness gates (`5180`) and controls.
- **Disposition:** Allow up to N existing dispatches per turn. Keep receipts, sentinels and maintenance at turn boundaries.

**F9 [P3] — No FamilyScheduler change is needed**
- **Problem:** Existing operations already cover what the plan wants to add:
  - Calling the existing `launched()` at selection registers lineage; `retire()` handles a positive never-started outcome.
  - The checkpoint already records each pending launch's lineage.
  - Recovery already treats a launch with no PID as live and rebuilds its path.
  - The current code already checkpoints up to six of these at once (W=6).
- **Disposition:** Record the premise as verified. Drop the Slice 1 stop condition and the rollback compatibility probe.

**F10 [P3] — Leave `_cleanup_children` alone**
- **Problem:** The repeated sweep is cheap: local exit checks only, with broker writes only when a child has exited. The plan's "guarded standalone entry" would add a branch for the heartbeat and spawn-submission callers.
- **Disposition:** Drop it from scope unless F6's measurements show it matters.

**F11 [P3] — Wrong names and inaccurate claims**
- `_drain_spawn_queue` doesn't exist. The real methods are `_drain_spawn_requests_from_queue`, `_drain_public_spawn_requests` and `_drain_internal_spawn_requests`.
- The per-turn flag, `_child_launch_started_this_turn`, is never named.
- Invariant 1 overstates isolation: the current worker is a bound method that reads `_active_cleanup_deadline`, `_db_path` and `_weft_config`. List those cross-thread reads.
- Calling `launch_task_process` from several threads at once hasn't been checked. `set_executable` changes a global, and multiprocessing's child bookkeeping and resource-tracker start are shared state. This is harmless under the GIL but not on a free-threaded 3.14 build. Add the check to Slice 1's reading list.

**F12 [P3] — Crash-point tests need a stated method**
- **Problem:** Killing a real Manager at an exact point would need a test-only hook in production code.
- **Disposition:** Specify the existing in-process staged-successor pattern (`_reserve_and_checkpoint` in `tests/core/test_admission_recovery.py`), with multiple TIDs at different stages and a real child side effect keyed by TID.

## Observations (not actionable)
- **Accepted crash window:** up to N launches can be left unresolved instead of 1. Any single unresolved launch already stops all ordinary dispatch, so the outage condition is the same; only the operator's cleanup work grows. This stays within the disclosed bound.
- **Existing double-seed path:** an existing path can also seed an inbox twice if a checkpoint fails after seeding and the error policy is set to REQUEUE. The default policy is KEEP.
- **SQLite:** a receipt would no longer use up the turn. Controls still run before dispatch, so the May lesson's reasoning still holds.

The full review is saved at `/Users/van/.claude/plans/you-are-reviewing-do-synthetic-lagoon.md`. No repository files were changed.

</details>

### Dispositions

| ID | Disposition | Operative change / evidence |
|---|---|---|
| F1 | Accept | `_seed_child_inbox` is non-idempotent. Removed post-seed fence/return; authorization is the existing pre-seed check, with an exactly-once seed/work regression. |
| F2 | Accept | Confirmed no-committed-child exit branches. Named all three; require in-flight drain participation. Round 2 corrects the assumed timeout: non-destructive drain waits for producer closure and receipts without a new timer; destructive STOP retains its existing deadline. |
| F3 | Accept | Active launches veto `_has_actionable_leadership_work`; positive lower-primary proof now enters existing revalidating leadership drain despite active launches. |
| F4 | Modify | Inactive group does not prove missing outcome. Preserve exact unresolved request and accept late matching receipt across grace; test delayed publication. A global no-pending-results condition is insufficient by itself because publication can occur after that read and unrelated events could defer detection indefinitely. |
| F5 | Reject implementation switch | Groups are bounded registrations, not persistent threads. Keep existing ServiceTask lifecycle and identity transport; document raw-worker alternative rather than implement a second path. |
| F6 | Accept | Slice 1 now go/no-go before code; measure phase contribution, choose smallest justified N in 2..6, and measure the sweep contribution without claiming it dominates. Six remains the safety ceiling, not an asserted optimum. |
| F7 | Accept | Mirror lineage insertion and custody exception into MANAGER.18; generic non-PG fallback is one; implementation mapping and resume comment named. |
| F8 | Modify | No nested lifecycle drains. Ready facts end slice for an immediate next turn; retain per-authorization controls and pure due-deadline checks. This preserves responsiveness without mutating watcher state recursively. |
| F9 | Accept reuse; qualify evidence | Use launched/retire and update semantics/docstrings only; existing plural checkpoint/recovery shape verified. Removed speculative schema stop and runtime rollback probe. Startup W=6 alone is not proof of six concurrent OS starts. |
| F10 | Modify | Cost is not known from code size or local reads alone. Round 2 resolves the conflict with F8: skip the duplicate sweep inside explicit dispatch scope, retaining standalone/shutdown cleanup; measure its benefit and omit a new subset-cleanup API. |
| F11 | Accept | Corrected drain name, explicitly named turn flag and cross-thread reads; added concurrent multiprocessing audit/proof in Slice 1. No unsupported assertion of GIL/free-threaded safety adopted. |
| F12 | Accept | Specify staged-successor helpers and child test driver around production phases, no production-only crash hooks. |


### Cross-family round 2: verbatim findings

<details>
<summary>Focused review, 2026-10-22</summary>

**FAIL.** F2 is not closed, and the F8/F10 fix introduced an inconsistency between the plan's spec text and the code it keeps. F1, F3, F4, F6, F7, F9, F11 and F12 pass at e220748f, though F3 only works once F2 is fixed.

## Findings

**R2-1 (P1, F2 not closed). The fix assumes a time limit that doesn't exist for non-destructive drains.**
- The plan says non-destructive turnover waits "within its existing bounded drain window" and then describes what happens "at expiry".
  - Leadership yield and supersession have no such window. `_continue_shutdown_drain` only times out when `_drain_stops_children` is true (`weft/core/manager.py:8673-8676`), and `_manager_drain_timeouts` only adds a deadline in that same case (`manager.py:8239`).
  - Today a non-destructive drain waits for committed children with no time limit.
- The plan also says to "carry the existing … `_drain_stops_children` policy into late-worker cleanup". That isn't possible as written:
  - `_finish_graceful_shutdown` resets `_drain_stops_children = True` (`manager.py:4909`) before `_cleanup_task_resources` runs.
  - The worker's self-reap branch triggers on `context.stop_requested()` or `_active_cleanup_deadline` (`manager.py:1577-1578`), and both are set on every shutdown regardless of reason.
  - So "reason-aware late cleanup" needs new state, which conflicts with the plan's "existing policy" claim.
- **Failure scenario:** leadership proof arrives with zero committed children and two active launches. Under the F2 text, the "expiry" has no defined clock. If the implementer reuses the destructive clock or flag, the late detached launch goes into the kill branch at `manager.py:1584-1598`, which is the exact behaviour F2 was meant to prevent.
- **Suggested disposition (removes work):**
  - Non-destructive completion should wait until no owned launch group is still live. This applies to `_continue_shutdown_drain` (8685), the leadership no-children exit (4278) and `_begin_superseded_shutdown` (4893). It matches the existing unlimited wait for non-destructive children.
  - Dead groups with no trustworthy result stay as unresolved checkpoint obligations and don't block completion.
  - A later STOP switches to the existing destructive, time-limited path.
  - Delete the "at expiry", reason-carrying late-cleanup branch and its receipt-after-expiry test.
  - Hung workers block non-destructive drain the same way hung children do today. That isn't a new limitation.

**R2-2 (P2, introduced by F8 + F10). The proposed spec text contradicts the cleanup sweep the plan keeps.**
- The proposed [MA-3] text says "no recursive lifecycle drain occurs inside queue delivery". But the plan keeps `_cleanup_children()` in `_handle_work_message` (`manager.py:5460`) unless measurement justifies removing it.
- That sweep is a lifecycle commit made inside the queue pass. It writes terminal records, deletes exact reserved rows, writes service-owner registry entries, and clears `spawn_pending`/`active_tid` (`manager.py:4537-4601`). With bounded dispatch it now runs between authorizations, up to six times per turn.
- **Suggested disposition:** inside a reactor dispatch slice, make skipping the handler sweep required rather than measured-optional, and keep it for standalone entry and shutdown.
  - The turn-level sweep already ran (`manager.py:8381`).
  - The plan's own slice-yield rule already ends the slice when a sentinel is ready or a terminal retry is due.
  - The alternative is to soften the [MA-3] sentence. That is worse because it keeps the nested commit the plan's F8 disposition rejected.

## Verified (no action)
- **F1:** `_launch_child_task` seeds before checkpointing (1403-1413), and the existing final control check is the pre-seed one at 5539. Nothing between seeding and return sets `_draining`; `_register_tid_state` only appends state (`base.py:2436`). The post-attempt requeue branch at 5566-5574 stays effectively pre-seed only.
- **F3:** `_queue_has_pending` returns False for the reserved queues (5020-5023), so in-flight reserved rows don't create a second veto. The only vetoes to remove are 4123 and the early return at 4216. Correct once R2-1 handles the zero-children exit at 4278.
- **F4:** the active handle is cleared before the terminal event is published (`service.py:576-584`). Events carry `request_id` set to the child TID (1489). Late commits don't depend on `_active_child_launches` (1963 → 1826). The premise holds.
- **F7:** both exact replacement anchors exist once in 03 and once in 07.
- **F9:** `launched` is idempotent (`family_scheduling.py:286-290`), `retire` contracts the path (310-327), and `_recover_scheduler_paths`/`_scheduler_evidence_live` treat a missing PID as live/unknown (2615-2620, 2307-2309).
- **F11:** `ctx.set_executable` is called per launch (`launcher.py:203`), and the plan correctly names it for audit.
- **F12:** the `_reserve_and_checkpoint` test helper exists (`test_admission_recovery.py:61`).

## Out of scope (no action needed)
- The order seed → pre-launch checkpoint leaves a crash window where a successor can requeue an already-seeded row. This exists today; concurrency only raises how many requests can sit in that window, within the accepted bound.
- `_drain_active_child_launches_for_cleanup` drops tracking for everything when no worker is active anywhere (8056-8062). This is already on the plan's audit list; the durable checkpoint keeps custody.

No standing invariant needs reversing. No RECONSIDERATION REQUESTED.

</details>

| ID | Disposition | Operative correction |
|---|---|---|
| R2-1 | Accept | Verified `_continue_shutdown_drain` and `_manager_drain_timeouts` time only destructive drains; `_finish_graceful_shutdown` resets the flag. Removed invented turnover expiry and reason-aware late cleanup. Non-destructive completion now waits for actual worker producer closure plus result drain, not inactive group handles; unresolved dead-group custody survives completion. Destructive timeout predicates include active launches with zero committed children. |
| R2-2 | Accept | No-nested-lifecycle spec and measured-optional handler sweep conflicted. Require dispatch-scoped sweep suppression with try/finally, preserve standalone/shutdown behavior, and prove both call paths. |


### Cross-family round 3: verbatim verification

<details>
<summary>Final focused verification, 2026-10-22</summary>

Both fixes pass. R2-1 has one wording change I'd recommend; neither fix reverses an invariant. I checked them against the live code at e220748f. The review is also saved at `/Users/van/.claude/plans/you-are-reviewing-do-replicated-moler.md`.

## R2-1 — PASS (one suggested wording change)

- **Drain reason in cleanup:** `_finish_graceful_shutdown` sets `_drain_stops_children` back to True (`weft/core/manager.py:4909`). So the plan is right not to rely on cleanup knowing why the drain started, and that wrong assumption is gone.
- **No invented timer:** `_manager_drain_timeouts` (8239–8278) only sets a deadline for a destructive drain with children. A leadership drain only gets its recheck interval. "Non-destructive drains have no timeout" matches the code.
- **The barrier really means the worker is done:** in BaseTask's worker runner (`weft/core/tasks/base.py:750–768`), the result is published before the thread removes itself from `_worker_threads` in `finally`. It then wakes the reactor, so no timer is needed. ServiceTask can clear `active_handle` while the thread is still running (`service.py:412`), so the plan is right not to trust it.
- **Only launch workers:** Manager starts worker threads only through its one launch group (602/1487), as the plan says.
- **Gaps the plan closes:** three places finish today without checking for live workers:
  - the no-children leadership yield (4278–4287). It can only run once no launches are active, because active launches count as work that blocks yielding (4123).
  - `_begin_superseded_shutdown` with no children (4893–4897).
  - the finish step in `_continue_shutdown_drain` (8685).
- **Destructive STOP:** it reuses the existing drain deadline and the existing late-worker self-reap in `_drain_active_child_launches_for_cleanup` (8045). No new timers.

**Suggested change A1 (check order matters):** `_has_worker_activity()` (`base.py:822–824`) checks for pending results before it checks for live threads, and existing code already uses it (1280, 8056, 8283). If the finish decision uses it, this sequence can happen:
1. The check finds no pending results.
2. The worker publishes its result and removes itself.
3. The check finds no live threads.
4. `_finish_graceful_shutdown` runs, and line 8367 applies the receipt after the `*_drained` event has already been published.
5. The newly launched child then gets stopped by `_terminate_children` during cleanup, which a non-destructive drain must never do.

The plan's wording ("wait for actual thread exit, then drain remaining results") already gives the safe order. I suggest adding one sentence: check `_has_active_worker_threads()` first, then `_has_pending_worker_results()`, and don't use `_has_worker_activity()` for this decision. Also add a test where the result is published between the two checks.

## R2-2 — PASS

- The sweep at `manager.py:5460` is the only cleanup inside `_handle_work_message`.
- The turn-level `_cleanup_children` at 8381 runs on every non-draining turn before both dispatch paths (8391 and 8405). So "turn-level cleanup already ran" is true.
- Skipping the handler sweep is now required, but only inside a try/finally dispatch scope. The standalone handler sweep and shutdown cleanup stay. That agrees with the [MA-3] text "no recursive lifecycle drain occurs inside queue delivery," so the contradiction is resolved.
- No change needed: the scope has to cover both `super()._process_reactor_turn` (8391) and `_drain_public_spawn_requests` (8405). The plan's plural "watcher dispatch paths" already covers that.

## Invariants

MF-5/MF-6, MANAGER.11, reactor ownership, no new timers, and no missing-evidence replay all still hold. RECONSIDERATION REQUESTED: none.

</details>

A1 disposition: accept. The shutdown section now explicitly checks actual live worker threads before pending results, forbids the oppositely ordered `_has_worker_activity()` shortcut at that boundary, and requires an inter-check publication regression. This clarifies the already-reviewed producer-closure ordering; no policy change.

Plan-authoring verification, 2026-10-22: `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py` passed (4 tests); `bin/check-doc-paths` passed; `git diff --check` passed. Existing operative paths and symbols checked against the source, including `_reserve_and_checkpoint` in the recovery tests. No runtime tests or performance claims were made for unimplemented behavior. The index remains draft until implementation is complete.


Owner review requirement (2026-10-27): before completion, perform both a fresh-eyes implementation review and a separate other-model/cross-family review on the final combined delta. Both must explicitly answer: Is it correct? Is it elegant enough for an expert systems programmer? Does it use the underlying data structures properly or overcomplicate them? Verify findings against actual code, firing tests, theory and measurement evidence; retain dispositions and focused re-review of accepted fixes. Earlier slice reviews do not replace these final passes.


### Pre-B clarification and prerequisite repair (2026-10-28)

Tier 1 Claude Opus 5.5 review (375.72s) identified B-F1: a consumed terminal
publication retry can remain expired while broker recovery starts fresh proof
grace. Real owner probes confirm stale[0.0] vs reset[15.0], then[14.0], with
reserved custody retained and real second-request dispatch. This existing
[MA-1.6a] no-spin violation becomes a new dispatch stall under B's proposed
veto. The repair is S2 in the runtime repair plan and must enter every arm's
common baseline before measurements. No B source code is implemented yet.

B-F2/F3/F4 accepted: sentinel observation is a hint; gate every delivery including
the first; scope the no-recursion statement to child-cleanup commits, preserving
normal worker-result receipts and other existing lifecycle owners. B-F5 accepted:
reuse existing Event/deadline readiness without another notify call. B-F6
accepted: measure yield turns and reject a meaningful latency regression unless
the owner explicitly accepts the observed tradeoff. Narrow child-only B policy
preserves non-child phase order; extra-pass maintenance belongs to conditional C2.
The operative paragraphs, proposed spec text and decision rule above are updated.
Independent round2 verification is pending; first review full text is retained
in the runtime repair plan and evidence directory, not silently treated as PASS.


Round-2 Tier-1 review (Claude Opus 5.5, 388.96 seconds) PASS for this revised
plan and S2 prerequisite, not for unimplemented B. Nonblocking R2-F1–F4 were
accepted: exact S2 normative-delta traceability, first-delivery wording,
predeclared latency distributions/repeatability rule, and advancement/retirement
for all child deadline families. Full review is retained in the repair plan.


### B implementation preparation (2026-10-28; not executed)

Parent inspected the proposed single-file source delta and four test families
against the actual delivery/cleanup owners. The production scope remains the
reviewed flag, pre-fetch Event/deadline check and exception-safe restoration.
Before implementation, reuse `startup_pg_manager` in
`tests/core/test_manager_family_maintenance.py` for the new B cases rather than
copying it into burst bookkeeping. That existing fixture owns physical PG
headroom, which a one-turn progress assertion needs. This is a test-path
adjustment, not changed capacity or worker settings. Retain existing burst
custody/shutdown and S2 keepers. Planned cases cover watcher/public dispatch,
standalone and exception paths, actual sentinel/retry readiness before fetch,
and bounded classification continuation with a first eligible dispatch.

The old-child readiness case stages a real process and exact reservation at the
cleanup owner; it does not claim a full initial launch path. The second request
uses actual launch. For retry expiry use a Manager-local advancing offset clock,
not a frozen global clock. Observe readiness nonconsumption at the gate return:
later unchanged convergence may settle the child in the same outer turn. Source
patch and test design are retained in
`/tmp/weft-implementation-20261027/admission-b-proposed-source.patch` and
`admission-b-implementation-design.md`. No B source change, test run or measurement
is claimed by this preparation; repaired A measurements precede applying it.

### Measurement harness review attempt 1 (2026-10-28)

Tier-1 Claude review of the draft measurement harness timed out at the bounded
540-second limit (540.58 s elapsed, exit 143), with no final result. Read/Grep
progress confirms tool activity, not a verdict or root cause for the timeout.
The invocation retained read-only containment and was terminated/cleaned up by
the bounded driver. No approval is inferred. A tighter operative-scope brief
and longer bounded window will be used for the next Tier-1 attempt; no fallback
review or measurement execution has occurred. Artifact:
`/tmp/weft-implementation-20261027/dispatch-harness-review.result.json`.


Measurement harness attempt 2 is in progress against frozen candidate SHA256
`6e77eb727158a343ceb8c9f120e2bef9028af782868841f73636535776abdb84`. The tighter
brief embeds the operative plan and full candidate, including quiet/trace and
opt-in/artifact-completeness additions, but excludes superseded review history.
The same contained read-only Claude invocation has a 900-second bound. Ruff,
format and AST checks passed; runtime validation, provenance warmup and all
measurements remain unrun. This is not an approval record. Candidate:
`/tmp/weft-implementation-20261027/dispatch_harness_quiet_draft.py`; invocation
and result prefix: `dispatch-harness-review2` in that artifact directory.


### Measurement harness review attempt 2: findings, gate still open

Claude completed in 553.45 s, exit 0, with a conditional DC-1 blocker and
additional findings. It explicitly did **not** read the required theory and
governance context. Parent accepts its source-verification evidence and concrete
findings, but not a completed review gate. The focused correction review must
read that context first and assess the final candidate against it. No runtime
measurement occurred before these dispositions.

| Finding | Parent disposition before measurements |
|---|---|
| DC-1 | Accept the stronger proof, not inferred PYTHONPATH. Integrate a small real spawned-function preflight inside the pytest invocation and require its verified artifact at completion. The prepared standalone script remains unused. |
| DC-2 | Accept a symmetric benefit rule before any data: all three candidate quiet elapsed values below both surrounding baseline minima. Explicit structural-neutral retention remains allowed for B/C1; C2 requires the elapsed benefit. |
| DC-3 | Accept named primaries: quiet elapsed, PING call-to-return and STOP handling latency. Other series are secondary; remove redundant paired-sign criterion. No significance claim or assumed independence of metrics. |
| DC-4 | Accept exact startup predicate guard; do not call a mutating owner merely to observe it. |
| DC-5 | Require observed B gate calls; absent coverage means unavailable maintenance evidence and an open measurement gate. No production-arm switch is added. |
| DC-6 / DC-7 | Accept derived handler-sweep counts and parent start-spacing as secondary evidence from existing raw records. |
| DC-8 | Keep the exact current terminal-proof oracle pending actual smoke evidence; inspect the existing owner contract before any relaxation. |
| DC-9 | Retain the declared 300-second experiment bound. No unmeasured increase; a timeout is a failed run to diagnose, not a passing sample. |
| DC-10 / DC-11 | Accept STOP-only closure attribution and preserving primary failures while recording diagnostic/cleanup errors. Sole cleanup/summary errors must still fail. |
| DC-12 / DC-13 | Accept accurate handoff/receipt naming and all four per-arm invocation recipes. |
| Craft | Share simple control/receipt observation where useful; remove unused cleanup timing and redundant source copy. Do not introduce a hook-table framework solely to replace a local branch chain. Preserve sufficient source provenance. |

<details>
<summary>Verbatim source-focused review, with its context omission disclosed</summary>

# Dispatch-cost harness review (frozen draft 6e77eb72…, Claude cross-family review)

**Overall: one conditional blocker (DC-1), which can be cleared by rewording alone.** The draft can produce valid A-versus-B comparisons. Before the B/C1/B batch runs, the comparison rule also needs DC-2.

**What this review is based on.** Everything below comes from reading code; I did not execute anything. Following your instruction to check source symbols first, I compared every symbol the draft touches against `weft/core/manager.py`, `tasks/base.py`, `tasks/service.py`, `tasks/multiqueue_watcher.py`, `control_probe.py`, `control_messages.py`, `task_evidence.py`, `serve_log.py`, `admission.py`, `tests/helpers/weft_harness.py`, `family_workload.py`, `pytest_pg_runtime.py`, `bin/pytest-pg` and simplebroker `_timestamp.py`.

**Context docs not read.** I did not read the program-theory, agent-context, runbook and lessons documents in the required order. I spent the bounded time on source verification instead, so treat any constraint judgments that depend on those documents as not independently checked.

## Symbol and boundary verification (all confirmed by reading)

- **Wrapped methods.** All 13 `_forward` targets, `_timed_manager_call`, `_process_queue_message`, `_manager_child_timeouts(*, now_ns)`, `_ManagerChildSentinelAdapter.wait`, `_finish_graceful_shutdown`, and the probe's `_write_probe_request` / `_read_matching_pong` exist with compatible signatures.
  - Call sites pass `child_spec`, `request` and `result` positionally, which matches `args[0]`.
  - `_record_admission_decision` is called with keywords only.
- **Patches take effect.** `install()` runs before `ensure_foreground_manager()` builds the Manager, so the handler and sentinel bindings resolve to the patched functions. `emit_serve_log_record` is looked up as a module global at `manager.py:816`, so the patch reaches it. Serve-log records are flat (`serve_log.py:139`), so `record["phase"]` and `record["elapsed_seconds"]` are valid.
- **Span nesting is correct.** The wrapper pushes the span id before calling the original. The original emits in its `finally` while the span is still on the stack, so `stack[-1]` is the span itself. `process_launch` is emitted directly at `manager.py:1830`, and the draft correctly excludes it.
- **Ready-receipt snapshot is correct.** Queue items are `TaskWorkerResult(value=ServiceWorkerEvent)`. The launch lane is started with `request_id=child_spec.tid` (`manager.py:1489`), so `ready_receipt_tids` really are child TIDs.
- **Post-STOP fence check is valid.** There is only one `_launch_child_task` call site (`manager.py:5553`), and it comes after `_control_allows_child_launch()`, which drains controls. A launch attempt entered after STOP's `control_return` is therefore a real fence violation, not an artifact of the observer.
- **Capacity and configuration.**
  - `bin/pytest-pg:231` sets `max_connections=300`, and `_observe_admission_usage` installs it through `set_max_connections` when ADMISSION_MAX_CONNECTIONS is 0.
  - `MANAGER_SERVE_LOG_ACTIVE` defaults to False.
  - The 60 s PING timeout is under the 1200 s cap.
- **Timestamps.** simplebroker hybrid timestamps keep nanosecond magnitude and are generated on the client host, so comparing them with `time.time_ns()` is a valid approximation.
- **Driver and opt-in mechanics.** The harness driver's 10 ms wait bound matches the manifest. The `skipif` is evaluated before the PG fixture is set up, and `pytest_plugins` follows the existing pattern in `test_measured_admission_pg.py`.

## Findings

| ID | Sev | Location | Evidence / counterexample | Strongest counterargument | Smallest disposition |
|---|---|---|---|---|---|
| **DC-1** | **Major (conditional blocker)** | `_manifest` → `"child_import_provenance"`; `_verify_artifacts` | The plan says warmup **must** verify that child imports come from the intended checkout. The draft only writes a sentence saying a probe is "required". Nothing implements the check, and `completion.json` is written without it, so an operator could reasonably think it is covered. | The arm delta is in `manager.py`, which runs in-process and is verified by the import assertion. The absolute `PYTHONPATH` is inherited by spawned children, so actual risk is low. | Either (a) add a warmup task targeting a test-module function that returns `weft.__file__` and assert it in the completion gate, or (b) change the plan and manifest to say child provenance is inferred from the inherited `PYTHONPATH`, not verified. Option (b) clears the blocker. |
| **DC-2** | Major (before C1 only) | Plan comparison rule | The rule defines a reproducible **regression** but has no symmetric **benefit** rule. "Keep C1/C2 only when … meaningful latency/completion gain" leaves "meaningful" undefined, which allows a post-hoc decision. | B is covered by the explicit neutral-timing clause, so A/B is unaffected. | Before B/C1/B runs, predeclare the mirror rule: all three C1 values below the minimum of both surrounding B batches. |
| **DC-3** | Medium | Plan comparison rule (multiplicity) | Under the null hypothesis, P(all 3 B values > max of 6 A values) = 1/84 per metric. There are about 8 series × 2 modes × 2 backends ≈ 32 comparisons, giving roughly a 30% chance of at least one spurious "reproducible regression" (less with correlated metrics). | The extra batch set and owner review limit the damage. | Name the primary series per mode (quiet: elapsed, PING call-to-return; trace: the two latency medians) and treat the rest as secondary. Drop the matched-ordinal clause: it follows automatically from "all three exceed the max". |
| **DC-4** | Medium | `_Observe.state` → `blocked_startup` | Real `_startup_dispatch_blocked()` returns False when `_startup_feedback` is empty, even if `_startup_blocked` is set (`manager.py:1848`). The copy omits that guard, so `held["blocked_startup"]` can count time when dispatch was not actually blocked. | It is a labelled diagnostic estimate. | Use `bool(startup) and (manager._startup_blocked or startup >= limit)`. |
| **DC-5** | Medium | `_observe_maintenance` / `_maintenance_summary` | The observer only sees B's gate if B literally calls `adapter.wait(0)` and `_manager_child_timeouts`. A B that uses another helper would report `maintenance_yields: 0`, which looks the same as A's zero. | The plan names those exact calls. | Analysis rule: in B, every spawn delivery passes through the gate, so `maintenance_gate_calls_observed` must be > 0. Otherwise treat B's maintenance metrics as unavailable, not zero. |
| **DC-6** | Medium-low | `_summary` | B's main operation-count witness is nested handler sweeps, but the summary only reports total sweeps. `inside_handler` exists only in the raw rows. | It can be derived from `events.jsonl`. | Add `handler_sweeps` and `handler_sweeps_per_launch_attempt`. |
| **DC-7** | Medium-low | `_latencies` | The historical premise metric was parent start **spacing** (mean 328 ms). Enqueue-to-start includes Manager boot and autostart, because parents are queued before the Manager exists, which dilutes B/C1 sensitivity. | Spacing can be derived from the raw files. | Add a parent start-spacing distribution as a derived series. |
| **DC-8** | Medium-low | `_stop_custody` "disappeared without terminal proof" | The terminal oracle reads only `ctrl_out`. A launched parent whose reservation was settled and which was force-reaped after the 15 s drain timeout needs `wrapper_lost` or task ctrl_out evidence. Any terminal that exists only in the task log would make the harness fail on a correct runtime. | Manager `wrapper_lost` failsafes probably cover this. | Check this on the first run, or align with the oracle used by the existing STOP custody tests. Not a blocker. |
| **DC-9** | Low-medium | `pytest.mark.timeout(300)` | One invocation covers a PG container start, 8 harness lifecycles, 4 STOP drains (each up to the 15 s drain timeout) and trace I/O. That is close to the budget. A timeout fails safely (no `completion.json`) but wastes batches. | Hitting it means a failed run, not a false measurement. | Owner decision: either size the budget from the sample count up front or accept it. This is a harness budget choice, not a cure for runtime failures. |
| DC-10 | Low | `_closure_summary` | Events are keyed only by name. If `_finish_graceful_shutdown` ever ran during PING-trial cleanup, `stop_write_begin_to_drain_finish_seconds` would be computed from the PING write. | Harness cleanup uses `stop_event` and `stop()`, not a drain. | Filter on `reason == "STOP command received"` and command `STOP`. |
| DC-11 | Low | `_sample` `finally` blocks | `assert not thread.is_alive()` in the inner `finally`, and `_summary` in the outer `finally`, can replace the primary failure; it survives only as `__context__`. | Raw rows are written first. | Compute the summary in a `try` that records its own error. Do not assert in `finally`. |
| DC-12 | Low | Naming and comments | `successful_launches_per_turn` counts worker handoff, not process success. The comment "including later … validation" is wrong: spec validation happens before `_launch_child_task`; only class resolution happens after. `ready_receipt_tids` ignores `kind="error"` receipts. | Cosmetic. | Rename or reword. |
| DC-13 | Low | Docstring commands | The examples pair quiet with SQLite and trace with PG, then say to run both modes on each backend. | The prose clarifies. | List the four invocations per arm explicitly. |

## Answers to the specific checks

- **Instrumentation cost and contamination.** Trace mode wraps `_cleanup_children`, which is exactly the call B removes, so trace timings are biased in B's favour. This confirms quiet mode must be the timing authority, as the plan says. Quiet mode's three hooks run after the original call and only touch the observer lock.
- **Failure cleanup.** The patch is held through harness teardown, and the control thread is released by the cancel events. DC-11 is the only gap.
- **STOP: real acknowledgment vs observation.** The draft measures handler return, drain finish and driver retirement, and labels each honestly. It never reads the published STOP ack on `ctrl_out`; reading it read-only after the measurement window is an optional improvement.
- **Reserved/source ownership.** The exact `peek_one(exact_timestamp=…)` reads on source and `manager._queue_names["reserved"]` are correct and read-only.
- **Launch-attempt definition.** Entry to `_launch_child_task` sits after the final control drain in the handler, which matches the plan's definition of "attempt at the final pre-seed fence".
- **Maintenance-yield attribution.** Sound as long as DC-5's rule is applied.
- **Ready-receipt snapshots and censoring.** Correct. Repeated completions mapping to the same next launch produce correlated samples (minor).
- **Phase nesting.** Correct.
- **Warmup.** One fresh-server warmup per invocation is adequate.

## The three questions

1. **Is it correct?** Yes for producing A/B evidence, once DC-1 is clarified. Wrappers forward real calls and take no scheduling authority. DC-2 and DC-3 must be fixed in the plan before C1 decisions, and DC-4 through DC-8 tighten diagnostics.
2. **Is it elegant enough for an expert systems programmer?** Adequate, not elegant.
   - Receipt and control observation is written twice (quiet hooks and trace `_forward`); one could be built from the other.
   - The long string-dispatch `if/elif` chains in `_before` / `_after` would read better as a per-method hook table.
   - Provenance is triplicated (git diff, all-source hashes, and a copied `manager.py`). HEAD plus diff plus the untracked-file hashes is enough.
   - The `harness_close_*` cleanup timing feeds no A/B/C decision and could be removed.
3. **Does it use the right primitives rather than overcomplicating?** Mostly yes: thread-local frames, `threading.Event`, `Queue.mutex` snapshots, and module-global patching of the real probe. There are no mocks and no framework. The one exception is DC-4, which re-derives a predicate instead of mirroring it exactly.

## Observations (not actionable for this unit)

- Trace mode swaps the harness's private `_context` attribute. It also flips `detach_stdio` for TaskMonitor children; this is inert because the harness disables the monitors.
- Under the harness, "normal autostart true" means `WEFT_TASK_MONITOR_ENABLED=0` and `WEFT_LIVENESS_MONITOR_ENABLED=0`, so few built-in launches occur.
- `ensure_foreground_manager` polls the registry inside the measured window. This affects every arm equally.
- There is one PING and one STOP per sample, so control-latency evidence is thin: 3 values per batch.

## Disposition

- **Scoped blocker:** DC-1. It is cleared by implementing the child probe or by rewording the plan and manifest.
- **Must be fixed before the B/C1/B batch, not before A:** DC-2.
- **Everything else:** not a blocker.
- **Reconsideration:** none requested.
- **Still required:** the unrun runtime validation remains mandatory after this review.

</details>


### Harness correction review, full-context pass (2026-10-28)

Claude completed in 563.90 seconds, explicitly reading required context first. One blocker, DC-14: the source probe incorrectly expects a payload argument for an empty mapping. This is a harness defect before warmup, not a measured runtime result. Corrections are being verified before integration; no measurements have begun.

<details><summary>Verbatim correction review</summary>

# Dispatch-cost harness correction review (Claude, cross-family; final draft e6049f83… against quiet draft 6e77eb72…)

**Verdict: one blocker, DC-14.** The new source preflight crashes on every run, so no measurement can ever complete. The fix is a one-line signature change. Everything else is P3 or a nit; two of those must be settled before the B/C1/B batch, not before A.

Nothing was executed in this review. Each claim is labelled: **[read]** means I read it in the repository source, **[inferred]** means reasoning from that source, **[supplied]** means it comes from your brief (the Ruff/format/AST results).

## Context actually read

I read these in the required order, in full, before looking at source:

- `docs/program-theory.md`
- `docs/agent-context/README.md`, `decision-hierarchy.md`, `principles.md`, `engineering-principles.md`
- Runbooks: `testing-patterns.md`, `review-loops-and-agent-bootstrap.md`, `hardening-plans.md`
- `docs/agent-context/lessons.md` (a pointer file only)
- `docs/lessons.md`, all of it. Its watermark in `docs/coalescing.md` is "(none)", so every dated section counts as startup reading.
- `08-Testing_Strategy.md` [TS-0], and the [TS-3.1] suppression-index section to check the new `noqa` comments.

There is no module theory in the tree; `program-theory.md` is the only theory surface. I did not read `adversarial-acceptance-probes.md`, `writing-plans.md` or the test-audit skill. They govern plan authoring, not this harness unit.

**How the corrected design measures up against that context:**

- **Owner confinement and facts vs hints (WT-2, WT-4).** Hooks forward the real call and only read Manager state on its own reactor thread. They take no scheduling authority. The custody oracle reads queue facts. No production seam, state store or lane is added (ALT-WT-010, ALT-WT-001).
- **Preflight uses the canonical path.** It runs the real TaskSpec → Manager → Consumer → host-worker path with real processes (engineering principles 1 and 4). The ALT-WT-011 "hidden preflight" ban does not apply: this is an opt-in test diagnostic, not the ordinary `weft run` path.
- **Load and timeouts (ALT-WT-017, TS-0).** The standard worker configuration and the 300 s bound are unchanged. DC-14 is a deterministic harness defect, not contention, so fixing it does not trigger the TS-0 stop condition.
- **Polling and queue use (ALT-WT-002, ALT-WT-019).** No new timers or caps. Artifacts are files, not queues.
- **Lessons, all respected:**
  - 2026-04-04: inherited `sys.executable` is now checked in the child.
  - 2026-10-14: work is prequeued before the Manager starts.
  - 2026-09-14: the inline Manager has titles off and shares the pytest GIL. That is the same for every arm.
  - 2026-10-28: the preflight harness is owned through `with` from the moment it is created.
  - 2026-10-06: no standard-library clock patches.
  - 2026-05-15: the receipt-consumes-turn rule is preserved for B.
- **testing-patterns:** Rule 7 holds; the expected paths are anchored to `rootpath` plus the manifest hashes, not just the same helper on both sides. Rule 9 is the weak spot: see DC-19.

## Prior findings DC-1 to DC-13

| ID | Status | Evidence |
|---|---|---|
| DC-1 | **Not closed (see DC-14).** The design is right: an integrated real preflight before warmup, and completion requires `source-provenance.json`. | [read] |
| DC-2 | Closed for A/B. Residual scope gap, see DC-18. | manifest + plan [read] |
| DC-3 | Closed. Primary series are named; matched ordinals are for reporting only. The residual multiplicity (3 primaries × 2 modes × 2 backends) is acknowledged as a limitation. | [read] |
| DC-4 | Closed. The guard now matches `manager.py:1846-1855` exactly. | [read] |
| DC-5 | Closed. In A, `_manager_child_timeouts` is only called from `next_wait_timeout` (`:8149`) and `adapter.wait` only from the shutdown loop (`:4767`), so neither runs inside a delivery. That makes `>0` observations specific to B's gate. | [read] |
| DC-6 | Closed. Nit: the sum is computed twice. | [read] |
| DC-7 | Closed. The mean spacing reduces to (last − first)/(n − 1), which matches the historical definition. | [inferred] |
| DC-8, DC-9 | Accepted limits, pending smoke. The preflight adds a ninth harness lifecycle and one more process launch under the same 300 s. | [inferred] |
| DC-10 | Closed. Filters on STOP command and the exact drain reason (`manager.py:4802`). | [read] |
| DC-11 | Closed. Semantics are sound (detail below). DC-20 is a nit. | [read/inferred] |
| DC-12 | Closed. `kind="error"` exists (`service.py:630`); the comment order matches `manager.py:5536-5553`. | [read] |
| DC-13 | Closed. Four explicit recipes. | [read] |

## New findings

| ID | Sev | Location | Evidence | Strongest counterargument | Smallest disposition |
|---|---|---|---|---|---|
| **DC-14** | **P1 (blocker)** | `dispatch_source_probe(_payload)` plus `client.submit(..., payload={})` in `_source_preflight` | [read] The probe is always called with no arguments, so it raises `TypeError: missing 1 required positional argument '_payload'`. Every invocation then fails at `assert result.status == "completed"` before warmup. The path: `_snapshot_payload({})` keeps `{}`; `spawn_requests.py:362` keeps it as `inbox_message`; `manager.py:2089-2093` seeds `"{}"`; `decode_work_message` returns `{}` (`targets.py:24-32`); `prepare_call_arguments` (`targets.py:39-75`, documented: "The empty mapping is structural and means 'no payload'") produces no arguments; the host worker calls `func()` (`host.py:169-175`). A `None` payload ends up the same way, via the start envelope → `{}`. Static AST/Ruff checks cannot catch this. | It fails closed: no false measurement, and the first smoke run would expose it. | `def dispatch_source_probe() -> dict[str, Any]:` and drop `payload={}`. |
| DC-15 | P3 | `_manifest` `source_hashes` keys vs the `_verify_artifacts` lookup | [read/inferred] Keys are built with `str(path.relative_to(root))`, which gives backslashes on Windows. The lookup uses `"weft/core/manager.py"`, so it raises `KeyError` on Windows. This lookup is new in this diff and goes against "one path on all platforms". | Fails closed; the experiment is currently run on macOS/Linux. | Key the dict by `relative` itself: `{relative: sha256((root / relative).read_bytes()) ...}`. This is simpler too. |
| DC-16 | P3 | Reduced provenance (`source_hashes`, `source.diff`) | [inferred] `git diff HEAD` leaves out untracked files. The old `rglob` hashing covered untracked `weft/*.py`; the new five-file set does not. So the claim "HEAD + diff identify Weft state" fails if an untracked module is imported. | The plan forbids new modules for B/C1, and C2 only touches tracked `_constants.py`. The arm still comes from HEAD plus the diff. | Record `git ls-files --others --exclude-standard -- weft tests` in the manifest and require it to be empty, apart from this test file while it is untracked. |
| DC-17 | P3 | Binding a completion to its invocation | [read/inferred] A skip cannot produce `completion.json`: `skipif` runs at collection, before any directory exists. That part holds. But a passing test's `DISPATCH_ARTIFACTS=` print is swallowed under default capture and xdist. If a parent directory is reused (for example for the allowed extra batch set), an earlier completion sits next to a failed or skipped rerun and can be miscounted. | The recipes already use a distinct directory per batch label. | Fail when the configured parent already contains a run directory, or state "fresh parent per invocation" in the recipes. |
| DC-18 | P3 (before C1 only) | Comparison rule vs summary | [read] C1 may be kept on "verified fewer receipt-only turns", but no summary field measures that. It can be derived from the `turn` fields on `receipt_commit_return` and `launch_attempt_enter`. Separately, the benefit rule does not say which backend it applies to. C2 batching is PostgreSQL-only, because SQLite keeps one authorization per turn. | A/B decisions are unaffected. | Before B/C1/B: (1) add `receipt_only_turns` to the trace summary and `_run_spread`; (2) declare "benefit is required on PostgreSQL; SQLite must show no primary regression". |
| DC-19 | nit | `_source_preflight` closure assert; `_verify_artifacts` flags | [read] The `all(...)` check runs on an empty list, so it always passes: `_stop_inline_managers` prunes to live entries or raises (`weft_harness.py:813-821`). `_closed` is necessarily true after a normal `with` exit (`:182-189`, `:786-798`). `checks_passed` and `harness_closed` are constants that the success path writes and `_verify_artifacts` then re-asserts. `code_filename` always equals `__file__`, because importlib fixes `co_filename` at import [inferred], so it adds no independent evidence. The real gates are the preflight raising and the manifest-hash cross-check. | Cheap, and it does check the artifact is intact. | Delete the vacuous parts, or keep them as integrity checks only. Optionally add the child's own test-module `__file__`. |
| DC-20 | nit | `_sample_harness` re-raise | [read/inferred] In `raise primary.with_traceback(primary.__traceback__) from exc`, `with_traceback(...)` does nothing, and `from exc` presents the later cleanup error as the "direct cause" of the primary. The wrapper also overlaps `WeftTestHarness.__exit__`, which already keeps the primary on a `RuntimeError` cleanup failure. It only adds value for non-`RuntimeError` cleanup failures, plus the `sample_failure` row. | It works, and the notes carry the details. | `raise primary from None`. Optionally compute `ready_receipt_tids`/`kinds` and `handler_sweeps` once each. |

**What I verified as correct in the changed code:**

- **Python version.** `sys.exception()`, `add_note` and `ExceptionGroup` all exist on the declared `requires-python >= 3.12` [read].
- **Exception capture in `finally`.** Inside a `finally` that runs during propagation, `sys.exception()` returns the propagating exception, because CPython pushes it on entry to the exceptional `finally` path. Inside a callee such as `_retire_control`, it sees the caller's active exception [inferred].
- **contextlib propagation.** When the generator re-raises the identical exception, contextlib lets the original propagate [inferred].
- **Sole errors still fail.** A lone summary or artifact error raises an `ExceptionGroup`; `events.jsonl` is written last, so it records any write errors.
- **Process topology.** Function targets run in a separate daemon host worker spawned by the Consumer (`host.py:497-507`), so the `pid`/`ppid ≠ pytest` assertions are valid.
- **Runtime identity.** It is installed in that worker from the transported payload (`host.py:166`, `:284-287`).
- **Executable equality.** Both spawn sites set `set_executable(sys.executable)` (`launcher.py:203`, `host.py:463`), so the executable check is sound. Windows venv behaviour is inferred, not verified.
- **Result round-trip.** Dict results come back as dicts (the existing workload relies on this).
- **Import path.** `tests/system/__init__.py` exists, so the `function_target` import works.
- **`noqa` comments.** Free-form `# noqa: BLE001 - …` matches existing repo practice. BLE is not selected, and the suppression index ignores comments without an approval pointer.

## Answers

1. **Is it correct?** Not yet. DC-14 makes every invocation fail before warmup. Once that one line is fixed, the integrated preflight correctly establishes child source selection and runs outside the timed windows. The failure handling keeps the primary error and still fails on lone diagnostic errors. Reduced provenance still identifies the arm through HEAD, the diff and the owner hashes (DC-16 is a small gap), and a skip cannot count as a passing measurement (DC-17 is about binding a result to its run, not about skips). DC-15 is a Windows defect that fails closed.
2. **Is it elegant enough for an expert systems programmer?** Better than the previous draft, and adequate:
   - Quiet and trace now share their causal records.
   - The provenance copy and the cleanup timing are gone.
   - The four recipes are explicit.
   - The remaining ceremony is small: vacuous closure flags (DC-19), a slightly over-general re-raise wrapper (DC-20), and duplicated comprehensions.
3. **Does it use the right data structures and primitives?** Yes:
   - Harness `with` ownership, `Event`s, and the `Queue.mutex` snapshot.
   - The real control probe.
   - Plain exception notes and `ExceptionGroup` rather than a custom error framework.
   - A real function task as the provenance witness, with no new seam.

   The one primitive misused is the empty-payload calling convention (DC-14).

## Observations (not actionable in this unit)

- About 12 primary comparisons remain. Under the null, P(all three > both maxima) is 1/84 per comparison, so the family-wide false-regression rate is roughly 13% if the comparisons were independent. The limitation is already declared.
- `simplebroker_pg` is not fingerprinted, only `simplebroker`. This predates the diff.
- The function worker imports the whole test module, including pytest and the harness. This is untimed, so it is acceptable, but it is the import-weight pattern from the 2026-10-28 lesson.
- A failed preflight leaves no artifact describing the failure; only pytest output records it. It still fails closed.

**Disposition:** blocker: DC-14. DC-18 must be settled before the B/C1/B batch. Everything else is a non-blocking P3 or nit. No reconsideration requested. Runtime smoke, the full correctness gates and the measurements are still mandatory, and no performance number is claimed.

</details>

Correction (2026-10-28): the quoted BLE001/inventory claim above is false. See [harness policy correction proof](#harness-policy-correction-proof-2026-10-28) for the actual raw-audit failure and repaired ledger.



DC-14–DC-20 dispositions before integration: accept DC-14 no-argument probe correction; accept DC-15 portable manifest keys; accept DC-16 explicit untracked-source accounting; accept DC-17 a fresh parent for each invocation. Accept DC-18 complete receipt-only-turn counts with partial-turn censoring and PostgreSQL benefit/both-backend nonregression criteria, now in the operative decision rule above. Accept DC-19 removal of vacuous closure flags/assertions; retain actual preflight ownership and manifest hash verification. Accept DC-20 simpler primary-exception rethrow without presenting later cleanup failure as its cause. Corrections and direct probes are pending; this disposition is not an implementation or measurement pass.


### Integrated harness smoke and author verification (2026-10-28)

Implemented the accepted DC-14–DC-20 corrections in the measurement harness. Isolated probes reproduce the empty-payload calling error, Windows key shape, untracked-file provenance gap, reused-parent ambiguity, and misleading exception chaining; corrected controls pass. Removed vacuous closure assertions/flags and redundant code filenames, preserving actual harness ownership and independently compared parent/child source hashes. Receipt-only-turn counts require completed observed turns; partial receipt turns are censored and quiet internal counts unavailable. Final typed integration uses existing `functools.partial` for heterogeneous artifact writes and narrows result dictionaries without new suppressions.

The first real smoke failed the exact custody-body assertion: low-level SimpleBroker `peek_one` defaults to `(body, timestamp)`. Both reads now request `with_timestamps=False`, verified against the actual backend API. This was a harness API error, not a scheduling failure. The failed run is retained at `/tmp/weft-implementation-20261027/dispatch-smoke-01.log`; it supplies no accepted measurements.

DC-8 disposition after source/spec check: retain the terminal oracle. Explicit STOP terminates/reaps tracked children under [MA-3]; voluntary leadership drain is non-destructive. The initial general detached-survival concern did not apply to this STOP path. Do not admit running snapshots as a shortcut around required STOP settlement. All four SQLite quiet STOP trials showed eleven exact source rows and one cancelled terminal; all later smoke custody artifacts also validated twelve accepted requests.

Final smoke source SHA-256 `aa038ff9529d279d060927be1e370b8ae66a30980ed9ac643b726d58df1d73d1`: SQLite quiet passed24.81s, trace24.38s; canonical PostgreSQL quiet45.01s, trace48.84s. Each selected case retained its real pipeline preflight, one warmup and three repetitions of throughput plus separate STOP. All32scenario directories and four completion markers were verified, with the same test hash and twelve custody rows in each of sixteen STOP artifacts. Normal12workers, capacity300/native admission, autostart and timeouts were preserved. The existing unmodified six/eight-parent acceptance also passed2SQLite6.65s and2PostgreSQL10.48s. Environment restored, owned test resources retired. These smoke runs validate the instrument and are not the accepted A baseline.

Author fresh-eyes: actual process/queue paths remain authoritative; forwarding observations neither call admission again nor select work. Useful-work time ends at validated results before joins; the wider observation interval retains control evidence. The first smoke's API mismatch demonstrates why static review alone was insufficient. Current limitations remain finite workload, correlated comparisons, quiet-hook overhead, partial-turn censoring and sample-held blocking estimates. Full raw evidence and exact commands: `/tmp/weft-implementation-20261027/dispatch-harness-final-review-evidence.md`. Final focused cross-family correction review is pending before freezing the comparison baseline.


### Final harness correction review (2026-10-28)

Claude completed in312.54seconds, required context read, no blocker and all three craft/correctness/primitive questions passed. DC-14–DC-20, type fixes and the body-only API correction are closed. DC-21 accepted: neutral C1 structural retention uses trace counts only and requires zero censored receipt turns in every compared sample; the operative rule and manifest now say so. This final change is metadata text only, not an observation or runtime change. DC-22 declined as cosmetic: the three correct artifact name/path pairs are local and explicit, and changing the callable shape again adds no behavioral protection. Broader observations remain accepted limits; no stopping-trial receipt count drives a performance decision.

<details><summary>Verbatim final harness correction review</summary>

# Scoped correction review: the dispatch-cost harness, e6049f83 → integrated source

**Verdict: no blocker.** The DC14–DC20 corrections, the type fixes and the `with_timestamps=False` fix are correct in the current source. I have two nits (DC21, DC22) and none of the corrections introduced a new correctness defect.

I made no edits, ran no tests or commands, and delegated nothing. I wrote no plan file because no write tool was available in this session.

Labels: **[read]** means I read it in the source myself; **[supplied]** means it comes from your evidence (smoke runs, probes, the git status snapshot); **[inferred]** means reasoning from the source.

## Context read (confirmed complete, in order)

1. `docs/program-theory.md`
2. `docs/agent-context/README.md`, `decision-hierarchy.md`, `principles.md`, `engineering-principles.md`
3. Runbooks: `testing-patterns.md` and `review-loops-and-agent-bootstrap.md` (I used its Scoped Change Review shape)
4. `docs/agent-context/lessons.md` (a pointer file only)
5. `docs/lessons.md`, the whole file. Its watermark in `docs/coalescing.md` is "(none)", so every dated section counts, through the 2026-10-28 entries.

There is no module theory. A search for `*theory*.md` under `docs/`, `weft/` and `tests/` finds only `program-theory.md` and two plans.

## Corrections, one by one

| ID | Status | Evidence |
|---|---|---|
| DC14 | **Closed** | [read] The probe now takes no arguments (`:228`) and `payload=` is gone (`:253-261`). With no payload, the call path gives the probe no arguments: `targets.py:24-27` and `:48-49` treat the empty mapping as "no payload". [supplied] All four smokes passed the preflight. |
| DC15 | **Closed** | [read] Hash keys are the forward-slash names from `imports` (`:121-124`). `_verify_artifacts` looks them up using `_source_modules` keys, which are the same literals (`:220-224`, `:1644-1647`). Path checks compare `Path`/`str(root / relative)` on both sides, so Windows separators normalise identically. |
| DC16 | **Closed** | [read] `git ls-files --others --exclude-standard -z -- weft tests` runs with `cwd=root` and splits on `\0` (`:125-145`). Git always prints `/` separators. The only allowed untracked file is the test itself, which matches the snapshot's lone `??` entry [supplied]. `.gitignore` only covers bytecode and caches here, so no imported module is hidden. There is no root `conftest.py` (glob), so the `weft`/`tests` pathspec loses nothing. |
| DC17 | **Closed** | [read] `parent.mkdir(parents=True)` with no `exist_ok` atomically claims a fresh parent (`:1688-1689`). Any existing parent raises `FileExistsError` → `pytest.fail`. `bin/pytest-pg --all` selects markers and is a single run (`bin/pytest-pg:546-548`), so the PG recipe does not reuse a parent. Selecting both nodes in one invocation now fails closed, which matches the docstring. |
| DC18 | **Closed (DC21 nit)** | [read] Turn keys are consistent: `_loop_iteration` increments first in `_process_manager_reactor_turn` (`manager.py:8350`), and receipts drain inside that same call (`:8356`, and `:8396` via `BaseTask._process_reactor_turn`, `base.py:1488-1495`). So `receipt_commit_return.turn` equals that turn's `turn_exit.turn`. Counting with set algebra over `(manager_tid, turn)` (`:995-1005`, `:1110-1114`) is right: censored = receipts without a successful in-window turn exit, and they are never classified. The fields are nulled in quiet mode (`:955-957`), and `_run_spread` drops `None` (`:1592`). The backend rule now names PostgreSQL for the benefit and requires no regression on both backends (`:209`). |
| DC19 | **Closed** | [read] `checks_passed`, `harness_closed`, `code_filename` and the empty-list closure assertion are gone. What remains is labelled as integrity checking (`:1642`): the worker-vs-expected equality and the cross-check against manifest hashes. Adding the worker-side `__file__` of the test module (`:223`) binds the module the child actually imported. |
| DC20 | **Closed** | [read/inferred] `raise primary from None` (`:1313`) leaves `__cause__` unset and suppresses the context, so the cleanup error no longer shows as the cause. The cleanup error survives in the note and the event row. Contextlib's `exc is value` path restores the original traceback. A lone cleanup error re-raises bare. |
| Type fixes | **Correct** | [read] The `isinstance(..., dict)` narrowings are real runtime checks (`:266`, `:1502`). The `span_id` ternary behaves the same as before, since `defaultdict.get(None, 0.0)` was already 0.0; it exists only for typing. The `partial` writers are typed `Callable[[], None]`. The `samples` rename stops reuse of the outer variable name. No suppressions were added. |
| `with_timestamps=False` | **Correct** | [read] Installed SimpleBroker: in the `BrokerConnection` protocol (`_backend_plugins.py:314-322`) and in `BrokerCore.peek_one` (`db.py:2802-2847`), `with_timestamps` defaults to `True` and returns `(body, ts)`. `simplebroker_pg` has no override. `accepted` holds bodies only (`:1479-1483`), so comparing body to body is now type-correct. That makes the smoke01 failure a fixture API error, not lost custody. [supplied] There were 12 custody rows in every smoke. |

The standing constraints are untouched: WT-2/4, ALT-WT-017/019, MA1.8/MA3, MF5 custody, TS-0, single owner, single worker, and fresh admission. The workers, timeouts and the 300 s bound are unchanged. No production file appears in the start-of-session git status [supplied]. No reconsideration requested.

## New findings

| ID | Sev | Location | Evidence | Strongest counterargument | Smallest disposition |
|---|---|---|---|---|---|
| DC21 | nit | `comparison_rule` (`:209`) vs. `receipt_only_turns` (`:1110-1115`) | [read] (1) The neutral-C1 rule compares receipt-only counts alone. An arm whose receipts are censored more often would show fewer receipt-only turns without any real gain, and the rule does not reference `receipt_turns_censored`, even though `_run_spread` records it (`:1576`). (2) The counts exist only in trace mode, and the rule does not say so. | In the PING window, censoring needs either a failed turn, which would almost certainly fail the sample, or a turn that straddles `ended`. `ended` falls after all results are in and the control thread has joined, so censoring is rare and visible in the series. | Append to the C1 clause: "(trace mode; every compared sample has `receipt_turns_censored == 0`, otherwise unavailable)". Alternatively, decline and record it as an accepted limit. |
| DC22 | nit | `_persist_sample` writes (`:1353-1366`) | [read] Each artifact name appears twice: as the label and in the bound path. If one is edited without the other, the error row names the wrong file. | It is correct now and purely cosmetic. | `functools.partial(_jsonl, rows=family)` typed `Callable[[Path], None]`, then `write(destination / name)`. This removes three path expressions. Optional. |

## Answers

1. **Is it correct?** Yes. Each accepted correction does what its finding asked, checked against the actual SimpleBroker, Manager, BaseTask and targets source. I found no new defect in the changed lines. DC21 is a clarification to the decision rule, not a measurement error.
2. **Is it elegant expert systems work?** Yes. Most corrections remove code: the redundant flags, the vacuous assertion and `code_filename` are gone. The rest uses the most direct mechanism available: an atomic `mkdir` claim, the existing body-only flag, `from None`, and stdlib `partial`. It adds no framework and no hooks. What remains is small: the duplicated names (DC22) and a typing-only ternary.
3. **Are the underlying primitives and data structures used properly?** Yes:
   - A filesystem `mkdir` acts as an exclusive claim.
   - `git ls-files -z` gives NUL-safe provenance.
   - `(manager_tid, turn)` set intersection and difference implement right-censoring correctly, given the verified place where the turn counter increments.
   - `peek_one` uses its declared body-only mode rather than tuple unpacking.
   - Exception chaining uses its intended semantics.

## Observations (not actionable this pass)

- **Stopping-turn receipts can be misattributed.** `BaseTask._process_stopping_reactor_turn` (`base.py:1440`, `:1498-1505`) drains worker results outside `_process_manager_reactor_turn`, so their receipts take the previous turn's number. This affects only receipt fields in STOP-trial summaries. `_run_spread` reads these metrics from `throughput` only, so no decision depends on them.
- **The untracked-file check is strict.** It rejects any non-ignored untracked file under `weft/` or `tests/`, not only `.py` files. That fails closed, which is intended.
- **One error message is broader than its cause.** An `OSError` from `_manifest`, such as git not being on PATH, now reports "require a fresh writable parent". The message does include the exception, and the wrapping predates this diff.
- **Probe hash differs from the final hash.** The isolated probes report `new_sha256 5fa84d2b…`, but the final source and every smoke record `aa038ff9…`. The probes therefore checked an intermediate candidate. I reviewed the current worktree file directly, so my findings do not depend on the probes. I did not compute the file's SHA myself.
- **Earlier observations still hold.** These are the roughly 12-comparison multiplicity, `simplebroker_pg` not being fingerprinted, the import weight of the function worker, and a failed preflight leaving no artifact.

**Disposition:** no blocker.

</details>

Final gates: six plan/spec hygiene tests passed and the two experiments skipped without opt-in, as required; those skips are not measurements. Ruff/format, suppression registry, full mypy479files, documentation paths and whitespace checks passed. First A measurements will use a dedicated detached baseline worktree and its own virtual environment, with the same configuration, source-manifest and actual-child preflight. The local .envrc is ignored rather than tracked; copied its existing environment loader and linked the same operator .env. Initial setup selected the primary checkout uv wrapper; no measurement ran, and both editables were restored to their owning checkouts before comparison.


### First A batches retained; B promotion (2026-10-28)

Common repaired A is `8123b129`, frozen in `/Users/van/.codex/worktrees/admission-baseline/weft`. All four normal12-worker batches passed: SQLite quiet24.76s, trace24.56s, PostgreSQL quiet45.34s, trace48.31s. Each has an empty source diff, same committed harness, verified actual-child source provenance, warmup plus three measured throughput/STOP pairs and complete raw artifacts. No B source or tests were applied before these results. Artifact inventory and complete series: `/tmp/weft-implementation-20261027/A-before-verified.json`; raw root: `/tmp/weft-implementation-20261027/dispatch-measurements/A-before`.

Quiet useful-work seconds: SQLite [4.033703209,4.430341833,3.975394167]; PostgreSQL [4.687358625,4.742724583,4.739174792]. These are baseline samples only. The returning A batches remain required; no benefit or regression is decided from this first batch. Original unchanged six/eight-parent acceptance passed both backends as recorded above.

B proposed [MA-3] paragraph is now promoted text-first against8123b129, before its implementation. The spec marks this permitted scope in flight without claiming a completed mapping. B retains one authorization and receipt-consumes-turn. Comprehension checks: free lane is not authority; no waiting for receipts; missing evidence never permits reseeding; newly ready child maintenance yields to its normal owner. The first real RED cases will now qualify duplicate-sweep removal and pre-fetch maintenance without changing original load/capacity.


### B implementation, falsification and A/B/A evidence (2026-10-28)
B implements only the promoted ordinary-delivery scope and pre-reservation child-maintenance observation. No C1 receipt allowance or C2 batching change is present. Manager SHA-256 `e5d98ca0a5428e5e23d1899704dc82619d679a4d699b313b556e90d36cf70fbc`; new-test SHA-256 `16c51addc4031c63c54eafa037fc9b97e70612b4b89eaf4acfa0966a7aab0787`. Tests live with the existing real family-maintenance fixture, rather than splitting its owned process/server setup across three files. This is a test placement adjustment, not reduced coverage.
Final RED: four intended failures and three passes on SQLite and PostgreSQL. Earlier fixture errors (body/tuple return shape and injecting before classification instead of after reservation) were corrected before accepting RED. GREEN: fifteen focused/keeper cases per backend, then the complete 51-case family gate on each backend, including unchanged six/eight-parent system acceptance. Ten isolated mutants produced twelve intended assertion failures; seven positive controls passed. Parent inspected the exact assertions, not only exit codes. All fourteen unique owned PIDs were absent afterward (28 marker-path records); environment and server inventory were restored. Exact logs, hashes, commands and controls: `/tmp/weft-implementation-20261027/b-final-manifest.json`, `b-red-final-manifest.json`, `b-mutant-verified-results.json` and `admission-b-prepare-mutant.py`.
All twelve A/B/A batches and 96 warmup/measured scenarios completed with matched harness/dependency provenance, actual-child source verification, normal twelve workers, unchanged capacity/autostart, and retained raw artifacts. A-before and A-after are the same clean8123b129 checkout; B uses that commit plus the frozen B source diff. Inventory: `/tmp/weft-implementation-20261027/A-after-verified.json`; combined evidence: `ABA-primary-analysis.json`; raw root: `dispatch-measurements/`.
Primary quiet samples below are seconds, in measurement order. The rule was fixed before A-before.
| Backend / metric | A-before | B | A-after |
|---|---|---|---|
| sqlite / elapsed_seconds | [4.033703209, 4.430341833, 3.975394167] | [4.153165042, 4.370222125, 4.131000125] | [4.328474375, 4.635464375, 4.419605875] |
| sqlite / ping_call_to_return_seconds | [0.014500083, 0.015966208, 0.012072375] | [0.013300041, 0.012900167, 0.011812708] | [0.015394291, 0.015246834, 0.121546916] |
| sqlite / stop_handler_return_seconds | [0.002635084, 0.002430875, 0.002530875] | [0.002668041, 0.002757834, 0.009014958] | [0.003194625, 0.003694834, 0.002961958] |
| postgres / elapsed_seconds | [4.687358625, 4.742724583, 4.739174792] | [4.739131542, 4.950271834, 4.742491833] | [4.935938334, 5.023572291, 5.119909375] |
| postgres / ping_call_to_return_seconds | [0.167205250, 0.174405792, 0.172763250] | [0.182297625, 0.175903292, 0.167479416] | [0.175720292, 0.178162459, 0.170451458] |
| postgres / stop_handler_return_seconds | [0.043082208, 0.049754041, 0.049808375] | [0.051847541, 0.053241042, 0.048893333] | [0.053029625, 0.047690584, 0.052007041] |

No primary quiet series meets the predeclared reproducible-regression rule; neither backend meets the elapsed-benefit rule. PostgreSQL elapsed and SQLite STOP have disjoint surrounding baseline ranges, so attribution is uncertain. The 9.015 ms B SQLite STOP sample and 121.547 ms returning-A PING sample remain in the record. Do not call these data a speedup or proof of causal neutrality. No extra batch is used to fish for significance.
Every trace repetition removes exactly24 handler sweeps. B actual gate-call counts: SQLite26/24/24 and PostgreSQL34/34/32; positive coverage closes the instrumentation gate. Maintenance yields are SQLite2/0/0 across1/0/0distinct turns and PostgreSQL10/10/8 across5/5/4turns, all sentinel-driven. The gate does not erase maintenance work, and its child-deadline projection remains O(tracked children). Total sweep and turn counts, blocked intervals and missing completion-proxy samples remain secondary evidence in the combined JSON.
Provisional retention decision: B qualifies under the explicitly allowed redundant-sweep-removal clause with no demonstrated primary regression. Retention remains subject to the completed-work review and outstanding correctness gates. This is not a performance-benefit claim. Independent reconstruction found all72 measured B PostgreSQL receipts ready and committed inside their authorization turn; SQLite72receipts were from prior turns. C1 still requires its own B/C1/B comparison. No concurrent workers are justified by these observations.
Author fresh-eyes read the exact production/spec delta and all seven tests separately after implementation. Scope/finally, inherited/public routes, non-consuming readiness, exact custody and later same-turn convergence agree with the approved design. Existing sentinel and deadline owners are reused; no new scheduler/cache/worker is introduced. Full note: `/tmp/weft-implementation-20261027/b-author-fresh-eyes.md`.
Tier-1 implementation review attempt1 timed out after540.26s, exit143, with no final result and empty stderr. Read progress is not approval. The same seven-part brief was retried with a900s bound calibrated to its size; result pending. Invocation remains the verified read-only Claude posture recorded above.
Full SQLite verification finished239.80s:5973passed,38skipped,3failed. The failures are measurement-harness hygiene: two direct `simplebroker.__file__` uses violate the architecture guard, and three reasoned BLE001 catches are absent from the global suppression inventory, causing two policy failures. No runtime case failed. Earlier blanket wording that final harness suppression gates passed is superseded by this actual frozen-source full-run result. Correct the harness provenance accessor and reconcile reviewed suppression evidence before closure; do not weaken the architecture or registry tests. Existing A/B/A artifacts remain tied to their recorded frozen source. Ruff/format, full mypy479files, doc paths and whitespace checks passed for B; policy failures remain open.


### B completed-work independent review (2026-10-28)

Tier-1 Claude retry completed455.76s, exit0, required context confirmed, no blocker and all three implementation questions answered yes. Its performance observation reflects the dispatch-time snapshot; subsequent A/B/A results are recorded above and were independently verified by a native research agent plus author calculations, not attributed to Claude.

Dispositions: F1 accepted as a measurement limitation: readiness hints can be stale or empty, costing one ordinary turn. Current artifacts count all observed gate yields but do not record matching-event counts at the following drain, so the stale-hint fraction is unavailable. Do not describe every sentinel yield as necessary settlement. No new polling, wake or adapter change is justified. F2 accepted: normalize whitespace and implementation-mapping style. F3 accepted: remove the classification test's duplicate explicit stop/cleanup and rely on its existing fixture owner, keeping release.touch. The staged-maintenance helper's cleanup remains because it owns the clock/fault scope. F4 declined: retain the inexpensive completed-result assertion for both real delivery routes; it proves these routes finish and supports clean teardown. No behavioral production correction requested.

<details><summary>Verbatim B completed-work review</summary>

## Context read

I read every required source in the order given, in full, before forming any judgment:

1. `docs/program-theory.md`
2. `docs/agent-context/README.md`, `decision-hierarchy.md`, `principles.md`, `engineering-principles.md`
3. Runbooks: `testing-patterns.md`, `review-loops-and-agent-bootstrap.md`, `hardening-plans.md`
4. `docs/agent-context/lessons.md`
5. All of `docs/lessons.md`. `docs/coalescing.md` sets the lessons watermark at "(none)", so every dated section counts as startup context. That includes the 2026-10-28 lessons on retry consumption and test ownership.

For module context I read:
- The B design, decision rules, slices and acceptance matrix in `docs/plans/2026-10-22-bounded-concurrent-manager-launches.md`, plus the B entries dated 2026-10-28.
- The promoted [MA-3] paragraph at `docs/specifications/03-Manager_Architecture.md:883-901`.
- [MANAGER.11], [IMPL.8] and [IMPL.10] in `07-System_Invariants.md`.
- The live owners: `Manager._process_manager_reactor_turn`, `_process_queue_message`, `_handle_work_message`, `_cleanup_children`, `_child_has_exited`, `_manager_child_timeouts`, `next_wait_timeout`, `_ManagerChildSentinelAdapter`, `_drain_child_sentinel_events`, `_run_managed_service_convergence`, `_continue_shutdown_drain` and `_terminate_children`; `BaseTask.process_once` and `_process_reactor_turn`; and `MultiQueueWatcher._process_queue_message`, `_drain_round_robin_pass`, `_drain_priority_queues` and `_drain_queue`.

## What I verified and what I didn't

**Verified by reading the files:**
- Every manager hunk is in the working tree at the diff's positions (`manager.py:646, 5170-5211, 5474-5478, 8361, 8409-8436`). The spec paragraph is at `03-Manager_Architecture.md:883-901`.
- The new tests are at `test_manager_family_maintenance.py:969/1067/1154/1270/1398`, and the exact assertion strings in the mutant log appear there.
- All identifiers in the tests and the RED notes exist. The RED note's `manager.py:2634` corresponds to the current line 2635 (`_family_request_identity` → `_build_child_spec`) once you allow for B's one added line at 646.
- The S2 retry consumption (`manager.py:4539-4540`) is in the baseline and untouched by this diff.

**Not verified:**
- The SHA-256 manifest hashes. My toolset can only read files, not hash them.
- Test and mutant results. You told me not to run tests, so those are your supplied evidence and I only read the assertions.

## Findings

| ID | Severity | Location | Finding and evidence | Strongest counterargument | Suggested disposition |
|---|---|---|---|---|---|
| F1 | P3 | `manager.py:5197` gate, adapter at `manager.py:457-477` and `546-549`, top-of-turn drain at `8344` | **The sentinel hint can be set when nothing needs servicing, which costs one turn with no dispatch.** Two ways this happens: **(a)** The observer thread puts its result, the reactor's `drain()` consumes it, clears the event and finds the queue empty, and only then does the observer call `_result_event.set()`. The event stays set with no result behind it. **(b)** A child exits after the top-of-turn drain but before `child_cleanup`. The outer sweep reaps it through `exitcode` (`4360-4362`) and calls `remove(tid)`, but the event already queued for it stays set. In both cases the gate yields once. The next turn's drain clears the event, and because `notify_activity()` always follows `set()` (`548-549`), that next turn starts at once. This is bounded and safe. | This is the accepted design: a hint is not death evidence and yields to an ordinary turn. Both windows are narrow races, not the usual path (exit → wake → drain → sweep). | No code change. In the B trace measurement, count gate yields whose next top-of-turn drain returned 0 matching events. That separates spurious yields from real ones in the yield-turn counts the plan already requires. |
| F2 | nit | `03-Manager_Architecture.md:881-901` | **Spec formatting drifts from the surrounding section.** There are two blank lines before the paragraph. The mapping uses plain `Implementation mapping:` where the same section uses `_Implementation mapping_:` (line 830). The new rule sits after that existing mapping list, not in the [MA-3] prose. I did not check whether backstitch or spec-hygiene tooling depends on the italic header. | The plan said "append before the next same-level section", and the text was promoted and reviewed in that position. | Use one blank line and the `_Implementation mapping_:` form. You could also fold the three-symbol mapping into the existing list as one bullet. The rule text stays as is. |
| F3 | nit | `test_classification_continuation_keeps_first_dispatch`, in the `finally` | **Teardown duplicated by the fixture.** `manager.stop(join=False); manager.cleanup()` already runs in `startup_pg_manager`'s teardown (`:116-118`), still inside the harness. The test's `monkeypatch.context()` has closed before its `finally` runs, so the clock and broker state are the same as at fixture teardown. The similar call inside `_staged_dispatch_maintenance` is justified, because it runs under the advanced Manager clock with the outage cleared. That lets custody settle even when the gate is broken, as the 2026-10-28 lesson requires. | `stop()` and `cleanup()` are idempotent. | Delete the two lines and keep `release.touch()`. |
| F4 | nit | `test_dispatch_reuses_outer_child_cleanup`, after `ready.exists` | **Possibly unneeded work in one test.** Both routes also release the child and wait for the outbox result `"done"`. B's claim (no nested sweep, convergence kept, real launch) is already proven by `ready.exists` and the ready file's PID. The full result path is proven separately in the yield test. | It cheaply shows that both B delivery routes produce a completed child and leave no custody leak. It also lets the child finish cleanly instead of overlapping teardown. | Your call. It is optional to trim. Keep it if you value the end-to-end check on each route. |

None of these findings is a correctness defect in the unit under review.

## Required checks

- **Forward-only scope.** The flag is set after `child_cleanup` and any forced convergence (`8400-8408`). It is cleared before the drain continuation, the leadership recheck and service convergence (`8437-8446`). Only two places read it: the handler (`5477`) and the gate (`5196`).
- **Exception restoration.** A `try/finally` wraps both delivery paths (`8411-8436`). The test raises a `BaseException` after a real reservation (exact reserved row, no seeded inbox). The standalone sweep before it is a positive control and the one after it is the target. The `leaked-scope` mutant fails on both routes.
- **Inherited and public delivery.** Both run inside the one scope and both reach `Manager._process_queue_message`. The public path goes `_drain_spawn_requests_from_queue` → `_drain_round_robin_pass` → `_process_queue_message`. So one gate covers both. In the yield test, a source left active also reaches `public_dispatch` and is captured there. An ungated public path would also trip the `launch` observer's settlement assertion.
- **Standalone calls.** The flag is false outside a turn, so the handler still sweeps. The test checks this, and the `no-standalone-cleanup` mutant fires.
- **Service convergence.** Its `_cleanup_children()` (`8001`) and the internal spawn drain (`8019`) run after the scope, so handler sweeps there are retained.
- **Shutdown.** `_continue_shutdown_drain` (`8697`, `8711`), `_terminate_children` (`4651`, `4765`) and `_cleanup_task_resources` (`8104`) all run outside the scope. Termination signals are applied at the start of `process_once` (`base.py:1438`). Inside the scope, STOP only calls `_begin_shutdown_drain`, which never calls cleanup. So the spec sentence "child-cleanup lifecycle commits do not run inside this ordinary delivery scope" holds against the actual callers.
- **Gate placement, including first delivery.**
  - The gate sits after the busy-lane, startup, evidence and candidate early returns, and before admission (`5207`) and fetch/reservation (`super()` → `multiqueue_watcher.py:1413`).
  - It runs per delivery with no earlier state needed, so it covers the first one. The yield test injects at the entry of the `queue_dispatch` phase. At that point the outer sweep has already seen the child alive or the retry not yet due, which is the boundary the acceptance matrix specifies.
  - A yield returns `False` without adding the source to `inactive_candidates`. A pass with zero progress breaks cleanly and doesn't notify (`1587`), so it doesn't spin.
- **Deadline predicate is serviced.** I walked `_cleanup_children` (`4529-4554`) and `_child_has_exited` (`4371-4385`). Each due deadline family is advanced or retired:
  - A sentinel recheck sets `last_liveness_probe_ns`.
  - Proof grace starts, then expires into publication.
  - A due retry is consumed, then either commits or is re-armed with a future deadline.
  - Retry-only projection (`8319-8323`) matches the skip at `4537`.
  - The grace boundary is consistent: `<= 0` due versus `<` grace.
- **Nonconsumption.** The gate only reads `Event.wait(0)` and builds the deadline list. The yield test checks readiness at the gate's return, and the `consume-sentinel` mutant fires. A gate that ran cleanup itself would be caught by the reuse test, which forbids any `_cleanup_children` call in dispatch phases.
- **Classification continuation and first progress.** The gate ignores `_family_classification_pending` and the general `next_wait_timeout`. The test first establishes that the gate's own predicate is false, then shows a zero continuation timeout is still present, then checks authorization precedes convergence. The `general-due-veto` mutant fires.
- **Mutations hit real owner boundaries, not setup.**
  - All ten production mutants edit the three B owners: the flag/handler, the gate and the `finally`.
  - Test faults are placed at real seams: `_build_child_spec` raises only for the exact reserved row, after the real reservation. The broker class's `write` fails only for the old child's `ctrl_out`. The clock is the module-owned binding, per the 2026-10-06 lesson.
  - The other wrappers only observe and delegate to the real methods.
  - The old child is staged at the cleanup owner, not launched through the Manager (an accepted limit). The new child goes through the full real launch path.

## The three questions

1. **Is the implementation correct?** Yes, judged by reading the code and your supplied test and mutant evidence. The scope is exception-safe. It covers exactly the two ordinary delivery routes. It leaves standalone, convergence and shutdown cleanup intact. The gate runs before admission and reservation on every spawn delivery, including the first. Its predicate is one that the cleanup owner always services, so a yield cannot stall indefinitely.
2. **Is it elegant, something an expert systems programmer would be proud of?** Yes:
   - One reactor-owned bit, scoped with `try/finally`.
   - One predicate placed at the single per-delivery owner that both routes share.
   - No new wake, timer, counter, API or abstraction.

   The tests are much larger than the ~25 production lines. Even so, each case maps to an acceptance-matrix row and at least one firing mutant, so this is coverage rather than ceremony. F3 and F4 are the only trims I found.
3. **Are the underlying data structures and primitives used properly, without overcomplication?** Yes. The gate reuses the adapter's existing `threading.Event` and the existing `_manager_child_timeouts` deadline projection, which `next_wait_timeout` already uses. It doesn't consume anything, and it relies on the observer's existing `notify_activity` and the zero-timeout wake from deadlines. The projection is O(children) per delivery attempt, which is an accepted limit.

## Observations (not actionable this pass)

- **Immediacy of the post-yield wake has no direct test.** It rests on the existing owners: the observer's `notify_activity` after `set()`, and due deadlines returning a zero timeout from `next_wait_timeout`. B deliberately adds no wake. `drive_until` with a 30-second bound would also pass a delayed wake, so the A/B/A latency and yield-turn series are what actually guard this.
- **A backwards wall-clock step is pre-existing and bounded.** If `sentinel_observed_ns < launched_ns`, the recheck deadline can fall due while `_child_has_exited`'s shortcut (`4378-4384`) skips the probe. The reactor then spins for up to `MANAGER_PID_LIVENESS_RECHECK_INTERVAL`. That spin predates B. Under B, dispatch is also held for the same bounded window.
- **No test separates "gate before admission" from "gate after admission".** The difference is cost only: one extra admission observation per yielded attempt. Correctness and custody are the same either way.
- **The tests depend on `_timed_manager_call` phase names** (`child_cleanup`, `queue_dispatch`, `public_dispatch`, `service_convergence`) as observation and injection points. Renaming a phase fails loudly (`assert injected and yields`) rather than silently.
- **The offset Manager clock shifts the Manager's wall time by about one grace period** relative to broker hybrid IDs during the terminal-retry case. The plan prescribed this and it passed on both backends. Still, it's a Manager-local deviation of wall time from broker-ID time to remember if that test ever becomes flaky (see the 2026-06-10 lesson).
- **Accepted limits not reopened:**
  - One authorization per turn; a receipt consumes the turn.
  - One worker.
  - Startup, admission and custody policy unchanged.
  - O(children) projection.
  - The gate observes but doesn't consume.
  - Later convergence may settle the yielded child in the same outer turn.
  - The old child is staged rather than launched by the Manager.
  - No change to worker count, timeouts or capacity.
- **Standing constraints, none reversed:** WT2/4, ALT-WT-001/002/015/017/019, MA-1.8/MA-3, MF-5/6, MANAGER.11/18, IMPL.8/10 and TS-0 all hold, as do queue-truth, single owner per worker, fresh admission before each reservation, the final pre-seed STOP fence (the handler's `_control_allows_child_launch` is unchanged and still runs before validation), no post-seed replay, no treating missing evidence as death, no source inactivation on a maintenance yield, and full original concurrency. No RECONSIDERATION REQUESTED.
- **Performance is not accepted.** B is pending the A/B/A gate. Only the first A batches are retained, and the B and returning-A measurements are still running.

**Verdict: no blocker**

</details>

B review corrections verified: classification continuation plus plan/spec hygiene7passed on SQLite1.98s; the real classification case passed on PostgreSQL6.59s at normal12workers. Environment restored. Production Manager hash is unchanged from the measured/reviewed B. B is retained as the current serial baseline; the harness-policy repair and latest-head full/CI gates remain open, so this is neither plan closure nor release approval.


### B PostgreSQL runtime verification (2026-10-28)

Normal12-worker PostgreSQL run passed5925tests,29skipped in364.63s. Command: `bin/pytest-pg --all -- tests -k 'not test_weft_uses_only_supported_simplebroker_surfaces and not test_approved_suppressions_match_the_standalone_registry and not test_real_suppression_index_check_is_clean'`. Only the three known harness-policy failures were excluded; they remain explicit post-correction gates. The first invocation omitted argparse's `--` and was rejected before any test ran; it is not a pass. Environment restored after the real run. The two opt-in experiment skips are not measurement passes.

### Harness policy correction proof (2026-10-28)

The first explicit catch-retention review completed357.26s. It supports all three catches as NET POSITIVE versus the actual executor/narrowing alternatives, but did not finish required runbook/invariant context, so it does not close the review gate. Its two mechanical findings are accepted: new rows must remain inside the registry table and pinned policy IDs/counts must change atomically (198groups,329directives). F3 uses the offered minimum durable alternative: preserve the complete one-time fault controls and mutation definitions here instead of adding routine tests for an opt-in measurement harness. F4 qualifies the control catch's protection to the normal join path. F5 adds the fallback-summary content assertion. F6 corrects, without rewriting, the older quoted review claim that BLE001 was unselected or untagged suppressions were ignored: both were false; the raw aggregate includes these catches. F7 retains old-source measurements as such and requires identical new harness bytes in all future B/C1/B arms.

The proposed correction uses standard `inspect.getfile` without changing the dependency guard. Actual manifest execution verifies exact module-path and sorted-source-hash equality with the old accessor. The original measured harness hash is `2a033aa341206bb82c1fae148e3806d85beae475c645af2e2073d722d5d33640`; corrected source is `e581597e4ad92df7c0bcb6232a976219a7cd342ba3013b53998654bf75484130`. The runtime/timing/observation code is unchanged. Eight positive fault controls pass; three isolated narrowing mutants fail their intended assertions. Review closure and post-correction policy checks remain pending.

<details><summary>Verbatim harness policy review (required context incomplete)</summary>

# Scoped-change review: TS-3.1 harness-policy correction (baseline `5c690ef0`)

I didn't run anything. Everything below comes from reading the live source plus the evidence in your brief. I treat the author-run probe results as reported evidence, not as something I verified.

**Context read, in order:** `docs/program-theory.md` (full), `agent-context/README.md`, `decision-hierarchy.md`, `principles.md`, `engineering-principles.md` (full), `agent-context/lessons.md`, and `docs/lessons.md` (full; the watermark is "(none)").

Partial reads:
- `testing-patterns.md`: lines 1–135 only (coverage policy, harness selection, design rules). I did not read Patterns 1–12.
- `review-loops-and-agent-bootstrap.md`: lines 144–285 only (scoped-change prompt and output standard).
- The plan: lines 110–154 and 756–830, plus grep hits. That covers the DC-11/DC-20 closure rows.

`08-Testing_Strategy.md` [TS-3]/[TS-3.1]: read in full. Also read: the registry rows 1–40 and 200–229 and its inventory line, `bin/ruff_suppression_index.py` (parser and reconcile paths), `tests/specs/test_ruff_policy.py` (pinned constants), `tests/architecture/test_import_boundaries.py:492–626`, and the full harness owners (`_manifest`, `_control`, `_summary`, `_latencies`, `_sample_harness`, `_retire_control`, `_persist_sample`, `_sample`).

**Not read:** `07-System_Invariants.md` and the test-audit skill. This unit touches no runtime invariant, so I judged the first low-relevance, but I have not confirmed that.

## Findings

| ID | Sev | Location | Evidence | Suggested disposition |
|---|---|---|---|---|
| F1 | **P1** | Proposed registry hunk `@@ -211,7 +211,10 @@` | The diff keeps the blank line after the `RUFF-SUP-378` row as context, then adds the 379/380 rows *after* it. `_table_rows` (`bin/ruff_suppression_index.py:231–235`) stops reading table rows at the first line that doesn't start with `\|`, so it never sees 379/380. `_index_directives` (`:595–599`) then fails with `unknown group RUFF-SUP-379`. `--write` refuses and `--check` fails. | Put the two rows directly under the 378 row, before the blank line. Keep the inventory edit where it is. |
| F2 | **P1** | `tests/specs/test_ruff_policy.py:63–145` (not in the diff) | These are pinned: `EXPECTED_GROUP_IDS` ends at `"RUFF-SUP-378"`, `EXPECTED_GROUP_COUNT = 196`, `EXPECTED_DIRECTIVE_COUNT = 326`. `test_approved_suppressions_match_the_standalone_registry` (`:721–723`) asserts all three. Tagging adds 2 groups and 3 directives (`scan_source_directives` counts only tagged comments, `:447–453`), so this test fails even once F1 is fixed. Golden Rule 18 (folded lesson "Exact Policy Fixtures Are Part Of The Atomic Registry") requires these constants to change in the same commit. | Add `"RUFF-SUP-379", "RUFF-SUP-380"` and set the constants to 198 and 329. That number is my arithmetic from the current constants; it needs confirming by rerun. This makes the unit three files, not two. |
| F3 | P2 | Proposed `Real proof` cells for 379/380 | The only proof is `/tmp/.../harness-policy-fault-probe.py`, which won't persist. TS-3.1 makes "the named real proof" the verification. The closest precedents (373, 366, 376–378) name committed tests. The eight controls need no broker or Manager, so they are cheap to keep. | **Owner decision.** Preferred: commit the eight controls as a fast test that is always collected. It should live outside the `skipif`-gated experiment function, e.g. a sibling module that imports the harness. Minimum alternative: put the verbatim probe source, the mutant definitions, the harness SHA tested and the results in the plan. Cite that section in `Real proof`, labelled as a one-time control rather than a standing gate. |
| F4 | P3 | Proposed 379 `Protected invariant` | "The owner joins it and rejects a failed control" is true only when the body succeeds (`_sample:1516–1518`). If the body has already failed, `_retire_control` (`:1317–1333`) joins the thread but never reads `failures`, so the transferred throwable is dropped. This is pre-existing and not made worse, but the row overstates it. | Qualify the wording: "on the normal join path". The behaviour gap is listed under Observations. |
| F5 | P3 | The author's probe, `_persist_sample` cases | It checks that `summary.json` exists but not that it contains the `summary_error` fallback. Verification step 3 asked for the fallback to be proven. The `assert not failures` check in `_sample` is never exercised (it is visible in source, so that's acceptable). | Add one assertion that `summary.json` contains `summary_error` in the summary-fault cases, then rerun. |
| F6 | P3 | Plan `:825` (DC review record) | It says "BLE is not selected, and the suppression index ignores comments without an approval pointer." The failure evidence disproves this: raw `--ignore-noqa` reports BLE001 = 136, and the global inventory counts untagged reasoned directives (TS-3 lines 278–283). This false premise is why the earlier review missed the ledger. | When you update the plan for F3, add a correction note to that record. Don't rewrite it. |
| F7 | nit | Measurement provenance | `_manifest` records `test_sha256` and `source_hashes`, so the edit changes the harness identity. The plan's batch rule (`:143–153`) needs one harness identity across every arm in a batch. | Record the old and new test SHA in the plan. Don't pool batches run with the old harness with batches run with the new one. Future B/C1/B batches use the new bytes in every arm. |

**Verdict: `blocker: F1, F2`.** Both are mechanical. As written, the diff can't pass its own step-4 verification. After those two fixes, nothing else in this unit blocks.

## Per-catch dispositions

**C1, `_control` `except BaseException` (`:770`).** Keep it: **NET POSITIVE.**
- The catch's job is to hand any throwable to the owning test, whatever its class. `threading.excepthook` silently ignores `SystemExit`. Pytest's outcome exceptions (`Failed`, `Skipped`) subclass `BaseException`. With an `Exception`-only catch, a dead control thread would leave `failures` empty.
- The control mutant confirms it (reported): `FatalProbe` escaped.
- Precedents: 347, 359, 376.

Executor/Future refactor: **NET NEGATIVE.**
- It doesn't remove the broad catch. CPython's `_WorkItem.run` does the same `except BaseException` → `set_exception`, so the catch just moves into the stdlib.
- It adds executor lifecycle, including the atexit join of worker threads.
- It changes failure shapes (`future.exception(timeout)` raises `TimeoutError`).
- It changes what runs between the useful-work clock start and control start, which plan `:151–153` fixes for all arms. That's a comparability change.

A bare Future still needs the catch. Narrowing to `Exception` loses fatal transfer.

**C2, `_persist_sample` summary `except Exception` (`:1349`).** Keep it: **NET POSITIVE.**
- What it protects: an active primary failure stays primary, and the later artifacts still get written. That holds whatever the error class.
- The scratch 8-class tuple still misses classes the source can raise: `AttributeError`, `RuntimeError`/`RecursionError`, a bare `StopIteration`, and `MemoryError`. It also includes `AssertionError`, which `_summary` never raises. It's a partial list with no principle behind it.
- The mutant confirms it (reported).

The generic outcome/callback helper: **NET NEGATIVE.** It still needs one broad catch, adds callback and return-type plumbing, and moves the fallback summary and the per-artifact error records away from their owner.

I also tried folding the summary computation into the write loop: **NET NEGATIVE.** It saves one directive but loses the self-describing fallback `summary.json` and needs a `nonlocal` capture for the success-path return.

**C3, `_persist_sample` write-loop `except Exception` (`:1370`).** Keep it: **NET POSITIVE.** Same reasoning as C2, plus the per-iteration continuation: catching outside the loop would skip later artifacts (precedent 363). The write mutant confirms it (reported). Leaving `BaseException` uncaught is correct; the fatal probes show it propagates.

## Groups, cardinality, and `inspect.getfile`

**Groups are lawful.** 379 has 1 directive (`BaseException` transfer) and 380 has 2 (`Exception`-only diagnostic containment). They protect different things with different exception classes, so they should stay separate groups. Merging into 359 or 373 would change rows that are already approved and whose proof is different.

The source tags match `_SOURCE_MARKER`, and the cardinality cells match `_APPROVED_CARDINALITY`. The IDs are unused (grep finds no live `RUFF-SUP-379/380`). Raw counts change only for BLE001 (133 → 136).

On tagging versus leaving them untagged: reasoned untagged BLE001 is lawful outside TS-3.1. There are 10 live examples in `weft/core/`, and with that route only the inventory line would change. I still recommend tagging. This review is the explicit review TS-3.1 groups are meant to record, the same shapes in test code (359, 373) are tagged, and the inventory alone is "a tripwire, not a second identity registry".

**`inspect.getfile(simplebroker)` versus `simplebroker.__file__`: use `inspect.getfile`.** The guard is meant to block coupling to SimpleBroker's non-exported API. `__file__` is set by Python's import system, not exported by SimpleBroker. `inspect.getfile` is the stdlib way to ask which file a module object was loaded from, and it returns that module's `__file__` when it has one. I don't consider it a lint-evading alias.

The alternatives are worse:
- `sys.modules[...].__file__` reaches the same dunder in disguise.
- `importlib.metadata` locates the installed distribution, which can differ from the imported module in editable installs and so weakens provenance.

Computing the string once, before the hash, keeps the emitted path and the sorted-tree hash algorithm unchanged in source.

## Design approval versus verification still to run

- **Approved as design:** keeping all three catches as tagged groups 379/380 with the corrected placement (F1) and policy constants (F2), and the `inspect.getfile` change.
- **Not verified by me:** every runtime claim. That includes the eight controls, the three mutants, metadata equality, and Ruff/mypy/index/pytest results.

**Required before the owner closes this:**
1. Metadata equivalence probe (step 1). It's still pending.
2. Rerun the eight controls and three mutants against the post-diff source with the F5 assertion added, and record the harness SHA tested.
3. Resolve F3: commit the test, or record the verbatim proof in the plan. Then replace the pending/approval wording in 379/380 with this review result and that evidence.
4. With F1 and F2 applied, run step 4. `--write` must exit 0 and add exactly the two expected index rows. `--check` must be clean. The architecture, policy and index tests must pass. Record the test SHA (F7).

## Observations (not actionable this pass)

- **Control failure lost on the primary path (pre-existing):** if the body fails, for example STOP's 60 s `control_seen` wait times out because the control thread failed first, `failures` is never reported. The root cause is lost. `_retire_control` could add each `failures` entry to the primary as a note.
- **Fallback return is unobservable:** `_persist_sample`'s fallback `summary` return value never reaches a caller on failure paths, because the exception propagates or an `ExceptionGroup` is raised. It only matters as the content of `summary.json`.
- **Guard over-matches dunders:** `_simplebroker_surface_violations` flags interpreter-set dunders other than `__all__`. Exempting import-system metadata may be a future guard refinement. Not proposed here, and no standing constraint is reversed.
- **Possible latent `KeyError` in `_summary`:** at `:1068–1072`, when `pong_matched` exists, it reads `controls["control_write_enter"]`, which the PING path in `_control` doesn't record. If this fires, C2 would contain it. I didn't trace this further.
- **Coverage pragmas:** the `testing-patterns.md` coverage policy asks for `# pragma: no cover` on broad `except Exception` arms. That's a coverage concern, not part of this lint unit.

</details>

The following is a one-time author-run control, not a standing CI gate. Run with the repo interpreter and `PYTHONPATH` set to the checkout root. Create the existing writable `ROOT` scratch directory first; the final JSON write is reporting, after assertions. It injects only at the diagnostic/probe dependencies and executes the actual thread and persistence owners.

```python
from __future__ import annotations

import json
import importlib.util
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

import pytest
import tests.system.test_manager_dispatch_costs as subject

if subject_path := os.environ.get("DISPATCH_PROBE_SUBJECT"):
    spec = importlib.util.spec_from_file_location(
        "dispatch_policy_subject", subject_path
    )
    assert spec is not None and spec.loader is not None
    subject = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = subject
    spec.loader.exec_module(subject)

ROOT = Path("/tmp/weft-implementation-20261027")
results: list[str] = []


class ProbeFailure(Exception):
    pass


class FatalProbe(BaseException):
    pass


for error in (ProbeFailure("ordinary control"), FatalProbe("fatal control")):
    observer = subject._Observe(set(), "probe")
    observer.first_receipt.set()
    observer.driver_ready.set()
    observer.manager_tid = "123"
    failures: list[BaseException] = []

    def fail_probe(*args: Any, **kwargs: Any) -> Any:
        raise error

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(subject.control_probe_module, "send_keyed_ping_probe", fail_probe)
        worker = threading.Thread(
            target=subject._control, args=(None, observer, "PING", failures)
        )
        worker.start()
        worker.join(2)
        assert not worker.is_alive()
        assert len(failures) == 1 and failures[0] is error, (
            "thread failure identity not transferred"
        )
    results.append(type(error).__name__ + ": exact control error transferred")

for site in ("summary", "first_write"):
    for has_primary in (False, True):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            observer = subject._Observe(set(), "probe")
            error = ProbeFailure(site)
            primary = ValueError("original sample failure") if has_primary else None
            real_jsonl = subject._jsonl

            def summary(*args: Any, **kwargs: Any) -> dict[str, Any]:
                if site == "summary":
                    raise error
                return {"test": "summary"}

            def write(path: Path, rows: list[dict[str, Any]]) -> None:
                if site == "first_write" and path.name == "family.jsonl":
                    raise error
                real_jsonl(path, rows)

            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(subject, "_summary", summary)
                patch.setattr(subject, "_jsonl", write)
                if primary is None:
                    try:
                        subject._persist_sample(
                            destination, observer, [], 0, 1, "quiet", primary
                        )
                    except ExceptionGroup as grouped:
                        assert grouped.exceptions == (error,), (
                            "sole diagnostic failure lost"
                        )
                    except ProbeFailure as escaped:
                        raise AssertionError(
                            "diagnostic failure escaped before later artifacts"
                        ) from escaped
                    else:
                        raise AssertionError("sole diagnostic error swallowed")
                else:
                    subject._persist_sample(
                        destination, observer, [], 0, 1, "quiet", primary
                    )
                    assert len(primary.__notes__) == 1 and site in primary.__notes__[0]
            assert (destination / "summary.json").exists(), (
                "later summary artifact not attempted"
            )
            events = [
                json.loads(line)
                for line in (destination / "events.jsonl").read_text().splitlines()
            ]
            assert any(
                row["event"]
                == ("summary_error" if site == "summary" else "artifact_write_error")
                for row in events
            )
            if site == "summary":
                assert "summary_error" in json.loads(
                    (destination / "summary.json").read_text()
                ), "summary fallback missing"
                assert (destination / "family.jsonl").exists()
        results.append(
            f"{site}, primary={has_primary}: later artifacts and error precedence preserved"
        )

for site in ("summary", "first_write"):
    with tempfile.TemporaryDirectory() as directory:
        observer = subject._Observe(set(), "probe")
        fatal = FatalProbe(site)

        def fail(*args: Any, **kwargs: Any) -> Any:
            raise fatal

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(subject, "_summary", lambda *args: {})
            patch.setattr(subject, "_summary" if site == "summary" else "_jsonl", fail)
            try:
                subject._persist_sample(
                    Path(directory), observer, [], 0, 1, "quiet", ValueError("primary")
                )
            except FatalProbe as seen:
                assert seen is fatal
            else:
                raise AssertionError("fatal diagnostic error swallowed")
        results.append(site + ": fatal diagnostic exception propagated")

(
    ROOT
    / (os.environ.get("DISPATCH_PROBE_RESULT", "harness-policy-fault-probe") + ".json")
).write_text(json.dumps(results, indent=2) + "\n")
print("\n".join(results))
```

Verified output and metadata:

```json
{
  "old_harness_sha256": "2a033aa341206bb82c1fae148e3806d85beae475c645af2e2073d722d5d33640",
  "new_harness_sha256": "e581597e4ad92df7c0bcb6232a976219a7cd342ba3013b53998654bf75484130",
  "module_path_equal": true,
  "source_tree_hash_equal": true,
  "actual_manifest_equal": true,
  "simplebroker_sha256": "5280db42103df9c96febba14e234c9080dcaa3a068e6c0ae2581181bd512760c",
  "positive_cases": [
    "ProbeFailure: exact control error transferred",
    "FatalProbe: exact control error transferred",
    "summary, primary=False: later artifacts and error precedence preserved",
    "summary, primary=True: later artifacts and error precedence preserved",
    "first_write, primary=False: later artifacts and error precedence preserved",
    "first_write, primary=True: later artifacts and error precedence preserved",
    "summary: fatal diagnostic exception propagated",
    "first_write: fatal diagnostic exception propagated"
  ],
  "mutants": [
    {
      "mutant": "control",
      "exit": 1,
      "assertion": "thread failure identity not transferred"
    },
    {
      "mutant": "summary",
      "exit": 1,
      "assertion": "diagnostic failure escaped before later artifacts"
    },
    {
      "mutant": "write",
      "exit": 1,
      "assertion": "diagnostic failure escaped before later artifacts"
    }
  ]
}
```

Mutation definitions, each applied alone to a disposable copy of the corrected full module: at RUFF-SUP-379 replace `except BaseException` with `except Exception`; at the first RUFF-SUP-380 catch replace `except Exception` with `except OSError`; at the second RUFF-SUP-380 catch do the same. Run the verbatim probe with `DISPATCH_PROBE_SUBJECT` pointing to that copy and a distinct `DISPATCH_PROBE_RESULT`. Require the exact assertion shown above, not only a nonzero exit. The unmutilated source passes all eight cases. These controls establish failure-policy sensitivity without creating a benchmark timing assertion.

### C1 clarification review and smaller alternative (2026-10-28)

The first C1 clarification review completed396.56s with required context complete. F4 accepted: the promoted spec must explicitly retain one new launch per turn. F1's preference for fewer broker reads is accepted, but its proposed active-branch assignment is not yet selected. A still smaller candidate keeps receipt handling unchanged and moves the existing allowance reset immediately after the initial bounded result drain. This makes only initial-phase receipts exempt; later receipts continue to block remaining dispatch exactly as B does. No new control read, fallback restructuring or state is needed. Source audit confirms no current initial-result callback authorizes a new launch. F2's categorical impossibility claim is not relied upon: pending-result and live-thread checks are separate observations, so stale recovery can clear an active entry before a now-queued result reaches the inherited pre-pass drain. The candidate retains late receipt fencing on that path.

F3/F5/F6 accepted as test-design corrections: pin the real receipt phase, observe exact reservation history, hold both sources for late-receipt priority/STOP, and establish real startup publication before expecting a new public authorization. F7 accepted as a limit: failed-launch source retry deadlines exist only when admission is enabled; KEEP/REQUEUE semantics remain unchanged. F8/F9 accepted for precise reactivation and wake comments. A new scoped plan review will assess the reset relocation and revised one-authorization/initial-phase-only wording before promotion or runtime edits. No C1 code is implemented yet.

<details><summary>Verbatim first C1 clarification review</summary>

I've finished the review. I made no edits. I couldn't write the plan-mode file because no Write or ExitPlanMode tool is available here, so the full review is below.

# C1 plan clarification review

**Verdict: blocker: F4 (P2).** Fixing it only needs one added clause in the spec delta. Everything else is a suggested change, not a blocker.

**Context read in order:** `docs/program-theory.md`, `docs/agent-context/README.md`, `decision-hierarchy.md`, `principles.md`, `engineering-principles.md`, the runbooks `testing-patterns.md`, `review-loops-and-agent-bootstrap.md` and `hardening-plans.md`, `docs/agent-context/lessons.md`, and all of `docs/lessons.md` (watermark none). I also read the plan from the start through the Acceptance Matrix, [MF-6] in 05, [MA-1.8] in 03, [MANAGER.11], [MANAGER.18] and IMPL.8/10 in 07, and the live code in `manager.py`, `base.py` and `multiqueue_watcher.py`. The `manager.py` I reviewed is the working tree with B applied. I couldn't check the B SHA-256 digest because I had no shell.

**Line numbers confirmed:** `_handle_child_launch_result` is at `weft/core/manager.py:1827`, and the receipt's flag assignment is at `:1839`. The launch's own flag assignment is at `:1481`. `_process_queue_message` is at `:5170`, with the flag gate at `:5183` and B's gate at `:5196-5203`. The two handler fences are at `:5489` and `:5557`. The turn function is at `:8356`, and the public fallback is at `:8419-8432`. `_drain_spawn_requests_from_queue` checks the flag at `:8597` and `:8609`. `_control_allows_child_launch` is at `:5157`.

## How a turn runs today

- **Turn start:** the turn drains worker results first (`:8370`), then control (`:8380`). It returns at `:8381-8387` if STOP set draining.
- **Delivery choice:** the full BaseTask delivery pass runs only if no launch is active (`:8412`). Otherwise the turn drains results in the active-launch branch (`:8417`).
- **Late work in the same turn:** after delivery, service convergence (`:8445`) can call `_drain_internal_spawn_requests` (`:8019`). That call goes through `_process_queue_message`, and today only the flag stops it after a late receipt.

## Findings

| ID | Sev | Location | Finding | Suggested disposition |
|---|---|---|---|---|
| F1 | P2 | Proposal steps 2–3 | **A smaller design exists that keeps control, priority and custody exactly as B has them.** Delete `:1839`. In the active-launch branch, set the existing `_child_launch_started_this_turn = True` after its result drain at `:8417`, with a comment: the dispatch point passed while a launch was running, so a receipt applied now continues next turn. An early receipt (`:8370`) is already followed by the turn's control drain (`:8380`) before any reservation, so no new fence is needed. A late receipt is then blocked from both the public-only fallback and convergence's internal drain, exactly as in B. This adds no state and no broker reads, and needs no restructured fallback. The original difference-register row ("B/C1 retain their call pattern") stays true, so the proposed correction can be dropped. It also stops the C1 arm from bundling a new control read on every spawn delivery with the receipt-allowance change, which would muddy the B/C1/B comparison. | Modify: adopt this design. **OWNER DECISION** if you want to keep the general pre-reservation fence for its own sake. |
| F2 | P2 | Step 2 rationale | **The cited path can't happen.** "A receipt delivered by BaseTask's pre-pass drain" never occurs: the full pass runs only when `_active_child_launches` is empty (`:8412`), and launch receipts exist only for active requests. A receipt in the post-pass drain follows a launch that already set the flag at `:1481`. The real path where a late receipt is followed by a reservation is `:8417` → `:8445` convergence → `:8019` internal drain. The proposal never names it, and step 3 alone leaves it open. | If the fence is kept, give convergence's internal drain as its C1 justification and correct the rationale. |
| F3 | P2 | Test "STOP already queued when the receipt is applied" | **The test as written would not fail if the fence were removed.** A receipt applied at `:8370` is followed by the control drain at `:8380`, which takes the STOP; the turn then returns at `:8387`. The successor is never reserved, with or without the fence. A public successor after a late receipt is already blocked by step 3. A falsifying setup exists only on the convergence path from F2: late receipt, STOP written after `:8380`, internal successor, convergence due or forced. The assertion should be that the exact message ID was never reserved, paired with a positive check that the observer works. Under F1, the test becomes: late receipt in the active-launch branch, STOP after `:8380`, both sources pending. Removing the `:8417` assignment then lets the fallback reserve the request and `:5489` release it, so the test fails as intended. | Modify: name the exact path in the test and target the mutation that applies to the chosen design. |
| F4 | **P2, blocker** | C1 [MF-6] delta | **The delta drops the only normative per-turn rule.** "must not accept another spawn message in the same turn" (`05:1356-1358`) is the only per-turn statement in 03, 05 or 07; I searched for it. The replacement only forbids reservations *while a request is active*. On PostgreSQL, receipts commit inside the launching turn, so the new text would allow a second authorization in that turn. The code still forbids it (`:1481`), and C2's delta assumes the rule exists. This silently changes a standing constraint. | Add before promotion: "A turn authorizes at most one new launch; launch authorization, not receipt application, consumes that allowance." Under F1, also add: "A receipt applied after a dispatch boundary that found the lane occupied continues in an immediate turn." |
| F5 | P3 | Test "internal priority after a late receipt" | Under the proposal, convergence at `:8445` can reserve the internal request in the *same* turn, depending on its schedule (`:7951-7961`). So "next-turn selection chooses internal" may be false or flaky. | Assert order instead: the internal ID is reserved before any public reservation, and public stays in source during the receipt turn. Under F1, "next turn" is deterministic. |
| F6 | P3 | Test "ready prior receipt" | The test must show the receipt was applied at `:8370`. On PostgreSQL, receipts are usually applied in the launching turn, so hold the real worker past that turn and wait until its result is queued before driving the next turn. On SQLite, the startup window of 1 (`:1851-1856`) blocks public dispatch until the received child's startup settles. That happens only if the snapshot read at `:1844` sees it. | Run on PostgreSQL, or assert that the snapshot is visible. Otherwise the progression check can fail in both arms for startup reasons rather than the flag. |
| F7 | P3 | "Error receipts keep their source retry deadline" | With admission disabled (`:2182-2188`, the SQLite default), `_schedule_admission_retry_for_source` returns early (`:2894`), so there is no deadline to keep. With the default KEEP policy nothing is re-dispatched. With a configured REQUEUE policy, C1 retries the restored row in the receipt's own turn (B retries it next turn). Still at most one retry per turn, and the missing deadline predates C1. | Limit the claim to admission-enabled configurations and note this case. No code change. |
| F8 | P3 | [MA-1.8] replacement | "After receipt processing and controls" leaves out startup settlement, which the sentence before it also names as a reactivation trigger. | Reword to "After that reactivation and the turn's control processing…". |
| F9 | nit | Comments | Update the `:1863-1865` comment. Under F1, add a comment at the new assignment, since the trace field `launch_started_this_turn` (`:878`) keeps its meaning of "allowance consumed". The harness defines receipt-only turns separately (`tests/system/test_manager_dispatch_costs.py:1110-1115`), so it is unaffected. | Apply. |

The failed-post-launch-checkpoint test is sound: the public gate is `_scheduler_undurable` (`:5187-5193`), and internal work is correctly not gated by it. Make the checkpoint failure come from a receipt applied at `:8370` in the same turn. In B the flag hides that gate; in C1 the test then shows the gate itself is what blocks dispatch.

## Answers to your questions

1. **Correct?** Yes, apart from F4. With step 3 plus the fence, a late receipt that leads to convergence's internal drain still drains control before reserving. Priority holds because the public-only fallback is skipped, and `:1481` keeps one authorization per turn. The written rationale is wrong in one place (F2).
2. **Elegant expert work?** Mostly, but it puts a general fence on every reservation to close a window that only exists in the active-launch branch. F1 handles that one boundary with the existing flag.
3. **Reuses primitives without needless state?** Yes, it adds no new state. F1 also removes the extra read and the fallback restructuring.
4. **Will the tests falsify the claimed boundaries?** Not as written for the STOP-before-reservation case (F3). The internal-priority test needs an order assertion (F5), and the progression test needs a pinned receipt point (F6).

**Strongest counterargument to F1:** setting the flag in the active-launch branch keeps the "turn consumed" overload, and a fence at the shared reservation point is more robust to future dispatch paths. My answer: no current path needs it. F1 keeps B's exact behaviour for late receipts. And a general per-reservation fence is a scope expansion that applies to every turn, not just C1, so it should be a separate owner decision.

**Smallest disposition:** adopt F1 (delete `:1839` and add one assignment at `:8417`). Add the F4 clause. Retarget the F3 and F5 tests to the F1 mutation. Make the F7 and F8 wording changes.

## Observations (not actionable in this pass)

- **PostgreSQL prediction:** all 72 traced PostgreSQL receipts committed in their launching turn, where `:1481` had already set the flag. So C1 does nothing structural on PostgreSQL. Under the proposal it would also add a ctrl_in read per gated spawn delivery. Worth writing down now that a PostgreSQL elapsed-time benefit is unlikely, so the outcome isn't read after the fact. This doesn't change the decision rule, and the B/C1/B arm still runs.
- **SQLite:** serialized startup may make the structural benefit smaller than the 72 prior-turn receipts suggest (see F6).
- **Redundant fence:** if the proposal's fence lands, the `:5489` handler fence becomes redundant, since it would run right after the new one. Removing it is an **OWNER DECISION**; I've kept it as you instructed.
- **Spec drift:** the ambiguity clause about pre-launch references in `03:469` is missing from `07:1163`. This predates C1.

No reconsideration requested.

</details>

Harness correction verification: the full default SQLite suite passed5976tests with38skips in177.15s after the correction, with no exclusions. The focused architecture/policy/index/plan/spec set passed144tests4.83s. Normal Ruff, full mypy479files, doc paths and whitespace passed. The formatter flagged only the newly preserved probe's Python code block; formatted that code and synchronized/re-executed the verbatim scratch probe after the full run. No production or measurement-observation semantics changed. Complete-context independent closure remains pending.


### Harness policy complete-context closure review (2026-10-28)

Claude completed297.00s, full required context confirmed, no blocker. F1-F7 all pass; all three catches independently reaffirmed NET POSITIVE. R1 accepted: record the immutable proof-containing commit in the ledger once created so plan retirement does not erase the retrieval cue. R2 accepted: a correction pointer now follows the old verbatim record. R3 accepted: probe instructions name the required writable scratch directory. Final SQLite5976pass38skip and full mypy479/format clean results postdate the review dispatch. Run the full PostgreSQL suite without exclusions before closing this correction. Future comparisons use one common corrected harness; no old/new measurements are pooled.

<details><summary>Verbatim complete-context harness policy review</summary>

**Verdict for this unit: `no blocker`.** All seven accepted round-1 fixes (F1–F7) check out against the current source: **PASS**. I made no edits, ran no commands or tests, and delegated nothing. I didn't write a plan file: you asked me not to, and this session has no write tool.

## Context read
I read these in the prescribed order, in full:
1. `docs/program-theory.md`
2. `docs/agent-context/README.md`, `decision-hierarchy.md`, `principles.md`, `engineering-principles.md`
3. Both runbooks, `testing-patterns.md` and `review-loops-and-agent-bootstrap.md`
4. `docs/agent-context/lessons.md` and all of `docs/lessons.md`
5. `08-Testing_Strategy.md` (all of it, including [TS-3] and [TS-3.1])
6. All 1,506 lines of `07-System_Invariants.md`
7. `skills/test-audit/SKILL.md`

I also read `tests/architecture/test_import_boundaries.py` and parts of `bin/ruff_suppression_index.py` (the source-directive scanner and the group-reconciliation code).

Partial reads, disclosed:
- **Plan:** lines 1–300 (operative rules) and 560–1344 (the DC-11 and DC-20 records, the B record and the proof section). I didn't read 300–560, the superseded Oct 22/27 review history.
- **Harness:** lines 1–230, 720–780 and 1310–1590.
- **Registry:** the header, rows 195–224, the raw inventory line and the generated index.

No module theory applies.

## Identifier and count checks (by reading and grep, not by running the tools)
- **Row placement:** rows 379 and 380 sit at `docs/ruff-suppression-registry.md:213-214`, directly after 378 with no blank line, so the registry parser will read them (F1).
- **Index:** generated entries are at `:417-418`, on the right symbols `::_control` and `::_persist_sample`.
- **Group count:** there are 198 human rows and 198 index rows, matching `EXPECTED_GROUP_COUNT = 198` (`tests/specs/test_ruff_policy.py:146`). IDs 379 and 380 are at `:143-144` (F2).
- **Directive count:** 330 tagged lines in `*.py`, minus 6 string fixtures in `test_ruff_suppression_index.py`, plus 5 in extensionless `bin/` tools, gives 329. That matches `EXPECTED_DIRECTIVE_COUNT`.
- **BLE001 count:** consistent with `BLE001=136`. I didn't run the raw audit, so this is grep-level only.
- **Comment form:** the source tags at `test_manager_dispatch_costs.py:772, 1351, 1372` match the scanner's required pattern.
- **Wording:** "on the normal join path" (F4) matches `_sample:1518-1520`. The fallback-content assertion (F5) is at plan `:1274`.
- **Plan records:** the F6 note is at plan `:1091`, and the original `:825` text is left unrewritten. The F7 hashes are at `:1093` and `:1303-1304`, with the future-arm rule at `:1091`.
- **Anchor:** `#harness-policy-correction-proof-2026-10-28` resolves to the heading at plan `:1089`.
- **No other reaches:** the harness has no remaining `simplebroker.` attribute access.

## Findings

| ID | Sev | Location | Evidence | Suggested disposition |
|---|---|---|---|---|
| R1 | P3 | Registry rows 379 and 380, `Real proof` cells | They link to a plan anchor. Coalescing removes retired plan files, and [TS-3.1] says a plan "must not become the lasting source of truth for an adopted exception". After retirement the only proof would be a dangling link. | When the owner replaces the pending Approval wording, add the adopting commit SHA so a `git show <sha>:docs/plans/…` cue survives retirement. This is not a blocker and doesn't reopen F3. |
| R2 | nit | Plan `:825` versus `:1091` | The F6 correction is about 270 lines away from the false claim, so someone reading the DC record won't see it. | Optionally add a one-line pointer after `</details>` at `:852`, outside the verbatim block. |
| R3 | nit | Probe script, plan `:1213` and `:1295` | `ROOT = Path('/tmp/weft-implementation-20261027')` is a scratch path. A rerun without that directory exits nonzero *after* all assertions pass. | Optionally note that the final write is reporting only, or that ROOT must be an existing writable directory. |

## Per-catch decisions (I independently affirm the prior decision)
I didn't see the scratch refactor source itself. I judged the alternatives as the plan describes them (`:1128-1150`), against the live source.

**`_control`, `except BaseException` (RUFF-SUP-379): keep, NET POSITIVE.**
- This thread is the only path that delivers control failures to the owner, which rejects them at `:1520`.
- With an `Exception`-only catch, `SystemExit` dies silently in the default `threading.excepthook`, and pytest's outcome exceptions are `BaseException` subclasses. `failures` would then stay empty and the check passes vacuously.
- The executor/Future alternative is **NET NEGATIVE**:
  - CPython's `_WorkItem.run` does the same `BaseException` catch, so the catch just moves into the stdlib.
  - It adds atexit-joined lifecycle.
  - It changes what runs inside the useful-work window, which starts before the control thread (plan `:151-153`). That breaks comparability across arms.
- A `Thread` subclass or a global excepthook would be the forbidden "same catch behind a wrapper", or would touch process-wide state.

**`_persist_sample` summary catch, `except Exception` (RUFF-SUP-380): keep, NET POSITIVE.**
- `_summary` is an arbitrary derivation over raw rows. A finite exception list is a partial family with no principle behind it.
- Fatal errors correctly propagate.
- Folding the summary into the write loop is **NET NEGATIVE**. It saves one directive but loses the self-describing fallback `summary.json` and the return value.
- A generic callback owner is also **NET NEGATIVE**. It separates the fallback and its recorded error from their owner.

**`_persist_sample` write-loop catch, `except Exception` (RUFF-SUP-380): keep, NET POSITIVE.**
- Each loop iteration must continue to the next artifact. An outer catch skips later artifacts, and `events.jsonl` is written last precisely so it records earlier write errors (precedent: 363).
- Narrowing to an OSError/TypeError/ValueError list misses things like RecursionError and arbitrary values in the rows, for no gain.

**Tagged groups versus leaving the catches untagged.** Untagged reasoned BLE001 suppressions are lawful under [TS-3]; with that route only the inventory line changes. Tagging is still justified:
- An explicit review exists, and the registry is where it is meant to be recorded.
- Similar test-harness catches (359, 366, 373) are tagged.
- The cost is two rows plus the pinned constants.

**Group boundaries are lawful.**
- 379 is one directive about fatal-inclusive transfer from a thread.
- 380 is two directives in one symbol with one invariant: ordinary-only containment, primary-failure precedence and continuation.
- Merging the two would blur `BaseException` versus `Exception` semantics. Splitting 380 would duplicate one invariant. Both follow the 377 precedent, and the cardinality cells match the grammar.

**`inspect.getfile(simplebroker)` versus `simplebroker.__file__`: use `inspect.getfile`.**
- The guard (`test_import_boundaries.py:543-552`) protects against coupling to SimpleBroker's non-exported API. `__file__` is import-system metadata, not SimpleBroker API.
- For a module, `inspect.getfile` returns exactly `module.__file__`. If there is none, it raises `TypeError`, an equivalent failure.
- It is the documented stdlib way to ask where a module was loaded from, so it isn't an alias used to dodge the gate.
- The alternatives are worse:
  - `sys.modules[...].__file__` is the same dunder in disguise.
  - `find_spec().origin` and distribution metadata can differ from the module actually imported.
  - Exempting dunders in the guard would change the architecture test, which is out of scope.
- No marker comment is needed: reverting fails the guard loudly.

**The three questions.**
- **Correctness:** yes. The changes are metadata access outside the timed window plus comment and registry governance. No runtime or timing code changed.
- **Elegance and locality:** yes. The catches stay at their owners and no wrapper or framework is added.
- **Primitives:** right ones. A stdlib accessor, `ExceptionGroup`, `add_note`, and the registry's existing grammar.

## What I approved versus what I didn't verify
- **Approved as design:** retaining all three catches, groups 379 and 380, the placement and constants, `inspect.getfile`, and the plan-hosted one-time proof.
- **Not verified by me:** all of these are your claims, not my observations:
  - the eight positive controls and three mutants;
  - metadata, manifest and hash equality, including both harness SHAs;
  - the 144 hygiene tests and the `--write`/`--check` exits.

  The mutation definitions at `:1339` are falsifiable as written. A mutant that widens 380 to `BaseException` would fail the fatal-propagation positives, so I don't require one.

## Required before the owner closes this
1. Replace the "closure pending" Approval wording in rows 379 and 380 with this review result and the evidence, ideally with the SHA cue from R1. Rerun `bin/ruff_suppression_index.py --check` after that edit.
2. Run `ruff format --check` and full mypy on the corrected harness. Neither appears in this round's evidence.
3. Run the full suite on SQLite at normal concurrency, and rerun PostgreSQL `bin/pytest-pg --all` **without** the `-k` exclusion of the three policy tests (plan `:1087`). That shows the original failures are closed.
4. Run every future B/C1/B arm on the new harness bytes. Don't pool them with results from the old harness (F7).

## Observations (not actionable this pass)
- These were already recorded and remain valid:
  - if the main body fails first, a control-thread failure is lost because `_retire_control` never reads `failures`;
  - the guard flags interpreter-set dunders other than `__all__`;
  - the broad `except Exception` arms lack the coverage pragma the testing-patterns coverage policy asks for.
- No standing constraint is crossed and no RECONSIDERATION REQUESTED.

</details>

Harness policy correction closed locally: full PostgreSQL `bin/pytest-pg --all` with no exclusions passed5928tests,29skipped in355.69s at normal12workers; environment restored. Together with the corrected full SQLite5976pass38skip,144focused policy checks, all eight fault controls/three mutants, metadata equivalence, lint/format/fullmypy and complete-context independent review, this closes the harness correction gate. It does not close admission C1/C2 or latest-head CI. The next B/C1/B sequence will use the corrected harness uniformly, with fresh B-before batches.


### C1 reset relocation: closure dispositions (2026-10-28)

Unit: initial-result-phase-only allowance reset, against repaired B at
`63dd4752fdff1e3a1da42187d881d0f1942565ce`. Manager source SHA-256 is
`e5d98ca0a5428e5e23d1899704dc82619d679a4d699b313b556e90d36cf70fbc`.
The selected design moves the reset and retains every late receipt assignment;
it supersedes the first clarification's proposed deletion and extra-fence design.
C2 and concurrency remain conditional/deferred. Measurement thresholds are unchanged.

The second review found only the startup-settlement continuation wording
blocking. The closure review returned **no blocker** after live source inspection,
with the following accepted refinements. These are plan/spec/test-design
resolutions, not executed correctness or performance evidence. Runtime edits,
RED/GREEN/mutations, implementation review and B/C1/B remain required.

| Review finding | Disposition and operative action |
|---|---|
| Closure F1 | Accept. The proposed and promoted [MA-1.8] second-sentence replacement qualifies receipts only in the initial bounded phase, keeps startup settlement and later immediate continuation, and preserves startup room, empty-source idling and blocked-backlog suppression. |
| Closure F2 | Modify as suggested. Keep public late STOP on PostgreSQL with all competing gates proven open at the shared decision in both real and mutant runs. Put late internal priority in the same-process replay keeper to bypass convergence cadence. Do not claim sensitivity until the receipt-assignment mutant makes the exact reservation assertion fail. |
| Closure F3 | Accept. Early STOP uses an internal successor, successful reservation history and a positive same-turn STOP assertion. Its mutant removes the initial control drain; it is an ordering keeper, not C1's RED. |
| Closure F4 | Accept. Require coupling comments at the receipt assignment and moved reset. Result callbacks cannot authorize; late receipts still close the allowance. Apply with runtime code. |
| Closure F5 | Accept. Require the receipt-or-settlement wake comment, retaining the unconditional existing wake and no new phase state. Apply with runtime code. |
| Closure F6 | Accept. Narrow the operative difference register, design, spec delta, slice and acceptance row; revise the May lesson in place with a historical marker. Update the [MANAGER.18] implementation mapping with code under strategy A. Historical verbatim reviews retain their original superseded wording. |
| Closure F7 | Accept. Exact-turn tests use direct `process_once`; its return closes the reactor reservation interval. No `drive_until` substitute for the turn being asserted. |
| Round-2 F1–F8 | Accept with closure refinements: retain both settlement and receipt liveness; observe reservations; demonstrate open gates; fully queue and phase-pin the receipt; use two eligible successors; update operative text; mark callback/reset coupling; use “new child launch” for B/C1 and attempted authorization only for C2. |
| First clarification F1/F2 | Modify/reject respectively. Adopt the preference for less machinery, but retain the receipt assignment. The categorical impossibility claim is false in the source-permitted queue/thread observation interleaving and same-process replay; the reset relocation handles those paths without an extra fence. |

Strongest accepted limitation: the reset relies on initial result callbacks not
authorizing. Mark both edit points and prove the exactly-one launch and late
receipt boundaries, rather than add a second state variable. Initial trace
allowance flags are not launch counters. PostgreSQL's earlier zero receipt-only
baseline predicts no structural win; no decision criterion is relaxed because
of that prediction.

<details><summary>Verbatim second C1 reset-relocation review</summary>

I made no edits and ran no tests, shell commands or delegation. There's no Write or ExitPlanMode tool in this session, so the full review is below rather than in the plan file.

# Round-2 review: C1 reset relocation (initial-phase-only exemption)

**Verdict: blocker: F1 (P2).** The fix is one clause in the proposed [MA-1.8] wording. The runtime change itself, moving the reset, has no blocker. It is correct and is the smallest design that keeps B's late-receipt fencing.

**Context read in full, in order:** `docs/program-theory.md`; `docs/agent-context/README.md`; `decision-hierarchy.md`; `principles.md`; `engineering-principles.md`; the runbooks `testing-patterns.md`, `review-loops-and-agent-bootstrap.md` and `hardening-plans.md`; `docs/agent-context/lessons.md`; and all of `docs/lessons.md` (watermark is none, confirmed in `docs/coalescing.md`).
- **Specs:** [MF-6] (`05:1309-1486`), [MA-1] (`03:122-139`), [MA-1.8] (`03:439-584`), [MA-3] (`03:740-859`), IMPL.8–10 (`07:832-870`), [MANAGER.11] (`07:1069`), [MANAGER.18] (`07:1133-1139`), and [TS-0] in `08`.
- **Plan:** lines 1–300 and the C1 record at 1341–1401.
- **Code:** I read the working tree. I could not hash the B SHA without a shell, so the identity of the B baseline is unverified.

## What I verified in live code

**Initial-phase callbacks.** I traced every route out of the initial worker-result drain. None of them authorizes work.
- `BaseTask._drain_worker_results` (`base.py:863-889`) calls only `_handle_worker_result`.
- `Manager._handle_worker_result` (`manager.py:1966`) has three outcomes:
  - service-worker events go through `ServiceTask._handle_worker_result` (`service.py:609`);
  - a direct launch-lane error raises;
  - an invalid value or another lane goes to the base handler.
- The event handler `_handle_service_worker_event` is defined at `:1929`, not `:1930` as the audit says (nit). It ignores `started` and `stopped` events. Its error and invalid-result routes act only when an active request exists (`:1938`, `:1952`). Non-launch events reach ServiceTask's no-op (`service.py:526`).
- The Manager registers exactly one service-worker group (`:602`) and has no other worker lanes.
- `_handle_child_launch_result` (`:1827`) and what it calls do not reserve work. That covers `_commit_child_launch_success`, `_handle_child_launch_failure`, `_apply_spawn_reserved_policy` (whose REQUEUE path only re-indexes the request), `_reconcile_launch_now`, `_advance_launch_custody`, `_ack_launch_custody`, `_release_startup_feedback`, `_resume_startup_dispatch`, `_register_managed_service_owner`, `_register_child_sentinel` and `_adopt_scheduler_evidence`.
- `_launch_child_task` has a single call site (`:5566`), inside `_handle_work_message`.
- A raise inside the initial drain exits before the moved reset. The flag keeps its earlier value, which is only more conservative.

**Late receipts.** Each receipt applied after the moved reset still sets the flag at `:1839`. That blocks:
- the public fallback (`:8423`);
- the convergence internal drain (`:8597`, `:8609`);
- watcher delivery (`:5183`).

The audit's F2 counterexample holds. `_has_worker_activity` (`base.py:822-824`) makes two separate observations. The service worker publishes result → stopped → thread-done, then exits (`service.py:575-586`, `base.py:759-768`). So stale recovery (`:1281`) can clear the active entry while a result is queued. That result then goes through the full-pass pre-drain (`base.py:1488`) with no active-map entry. Same-process replay returns early at `:1684-1685`, after the flag is already set at `:1839`. Keeping the receipt assignment is necessary.

**Custody after an initial-phase receipt.**
- `_checkpoint_spawned_admission` adds the TID to `_scheduler_undurable` (`:1557`). Only a successful publish clears it (`:1507-1512`).
- Public delivery is gated on `_scheduler_undurable` (`:5191`).
- A failure receipt with admission enabled re-blocks its lane (`:2894-2905`, `:2942`).
- Every reservation still takes a fresh admission decision (`:2947-2953`).

**Ordering after the reset.** Startup handoff, then stale recovery, then control (`:8380`, returning on draining), then refresh, leadership and cleanup, then any forced convergence, then delivery. The handler fences at `:5489` and `:5557` are unchanged.

## Findings

| ID | Sev | Location | Finding | Suggested disposition |
|---|---|---|---|---|
| F1 | **P2, blocker** | Proposed [MA-1.8] replacement | **The new wording drops a standing liveness guarantee for startup settlement.** The current sentence promises that after settlement *or* a receipt, the next turn re-evaluates work "without waiting for inactive-source rediscovery". The replacement keeps that promise only for "later receipts". Settlement can happen after the dispatch point, for example a reap during convergence that calls `_release_startup_feedback` → `_resume_startup_dispatch`. The code still wakes in that case, but the spec would no longer say so. A future change could remove that wake and still match the spec. | Replace the second sentence with: "Settlement or reactivation before that turn's dispatch point, including a receipt handled in the initial bounded result phase, may permit that turn's ordinary dispatch; later settlement or receipts retain an immediate continuation turn without waiting for inactive-source rediscovery." |
| F2 | P2 | Tests 2 and 3 | **The final queue state can't prove STOP came "before reservation".** `_release_unlaunched_spawn_request` puts the row back in its source under the same ID (`:5619-5624`, `:5424`). So "never reserved" and "reserved, then released by a handler fence" look identical at the end. Test 2 is a keeper that doesn't distinguish B from C1. Its only meaningful falsifier is removing or reordering the turn-start control drain (`:8380`), which is detectable only from reservation history. | Name the observer. Either use a pass-through wrapper on `_handle_work_message` that records exact `timestamp`s, or capture `spawn_reserved`/`spawn_reserved_released` records. Assert that the successor's ID never appears that turn and that `T{id}.inbox` is empty. Pair this with a positive check: the same observer records the successor in the progression test. Declare test 2's mutant: delete or move `:8380`. |
| F3 | P3 | Test 3 | **The mutant can pass trivially.** Deleting the receipt assignment at `:1839` produces a same-turn reservation only if the public gates are open at the late receipt. If startup is blocked (SQLite W=1) or `_scheduler_undurable` is non-empty, the public fallback returns at `:5193`. Pending internal work doesn't bypass the convergence throttle (`:7955-7961`). Then no reservation is reachable and the test passes without exercising the flag. | At the injection point, assert: `not _startup_dispatch_blocked()`, `not _scheduler_undurable`, `not _admission_evidence_unavailable`, and that the candidate is the public successor. Run on PostgreSQL with W=6 room. Reuse the phase-observer pattern at `test_manager_family_maintenance.py:1305-1330`. |
| F4 | P3 | Test 1 | **The receipt must be fully queued before the turn starts.** The worker publishes four envelopes. If only `started` is queued when the turn begins, the result lands in the active-launch branch (`:8417`). Both arms then fail for the wrong reason. | Before driving the turn, wait until `not manager._has_active_worker_threads()` and `manager._has_pending_worker_results()`. Thread exit implies the result is already published. Confirm the receipt ran inside the `worker_results` phase. |
| F5 | P3 | Test 1 / F4 clause | The delta now states "at most one new child launch per turn", which needs a firing test (principle 9). The existing `test_manager_stops_spawn_drain_after_child_launch_starts` stubs the launch. | Add a second eligible successor to test 1. Assert that exactly one is reserved in the receipt turn and the other stays in source. |
| F6 | P3 | Plan body, lesson | The operative plan still describes the broad design: lines 52, 181, 209, 215 and 247. The May 15 lesson (`docs/lessons.md:311-314`) still says an initial receipt "counts as launch work for that turn". Plan line 227 already requires revising that lesson, but the round-2 proposal mentions only the code comment. | When adopting, update those plan rows in the same revision. Revise the lesson with a `(revised …; was: …)` marker, keeping its reason: controls run before any reservation. |
| F7 | P3 | Proposed code comment | The reset is safe only because no result callback authorizes work. That dependency should be marked where the code is edited (principle 11, floor 1). | Append to the comment: "Result callbacks never authorize launches; one that did would be erased here." |
| F8 | nit | Wording | The plan's difference register and the accepted risk say "authorization"; the delta says "launch". In the code, the flag is set only once `_launch_child_task` registers an active request (`:1481`). Failures before that point (seed or class resolution, `:1408-1445`) don't consume the allowance. In [MF-6], "defers … to a continuation turn" could be read as promising an immediate turn even when admission retry gates it. Keeping "such as PING" preserves the existing contract example. | Use "launch" consistently for B and C1, and say "authorization" only for C2. Prefer "does not permit another reservation for the rest of that turn". |

The proposed [MF-6] text is otherwise consistent with the code: it states the one-launch rule, the initial-phase settlement, the gate order, the pre-seed STOP fence and no post-seed requeue.

## Answers

1. **Correct?** Yes, apart from the F1 spec wording. The code move preserves control-first ordering, internal priority, custody and fresh admission. Late receipts behave exactly as in B.
2. **Elegant expert work?** Yes. It is a one-line move that redefines when the existing allowance opens. It adds no state, reads or API.
3. **Reuses primitives without needless state?** Yes. It uses the existing flag, gates, handler fences and `_scheduler_undurable`. No new fence state is needed.
4. **Will the tests falsify the claimed boundaries?**
   - **STOP before reservation:** only with the reservation observer and the declared `:8380` mutant (F2).
   - **Internal priority after a late receipt:** test 3 catches it, provided the F3 preconditions hold. The mutant reserves public work at `:8421` before convergence's internal drain.
   - **Initial progression:** test 1 goes red if the old reset position is restored, given F4.
   - **Late receipt without an active-map entry:** test 4 catches F1-style designs.
   - **Checkpoint-failure custody:** test 5 does not distinguish B from C1, but under C1 it catches removal of the `:5191` gate.

## Test falsifier summary

| Test | Mutation that must fail it |
|---|---|
| 1. Initial progression | Restore the reset before `:8370` |
| 2. Early STOP (keeper) | Delete or move the control drain at `:8380` (needs the reservation observer) |
| 3. Late STOP / priority | Delete `:1839` (needs the F3 preconditions) |
| 4. Late receipt via replay | Delete `:1839`, or F1's active-branch-only assignment |
| 5. Checkpoint custody | Remove `or self._scheduler_undurable` (`:5191`) |

For test 4, I'd use replay only. Capture the real `ServiceWorkerEvent` with a pass-through wrapper. Republish it through `_publish_worker_result` after the reset, with no active launch. Assert that `_child_processes[tid].process` is the same object as before, nothing is reserved that turn, and the successor is reserved the next turn. The stale-recovery barrier needs much more setup (a timing barrier plus a module-owned clock) and hits the same pre-drain gate. The handler-level stale and replay path is already covered at `test_manager_family_maintenance.py:895-899`. Adding the barrier variant as well is an **OWNER DECISION**. For test 5, reuse the write-fault pattern at `test_manager_family_maintenance.py:851-861`.

## Strongest counterargument

The flag's meaning now depends on the phase: initial receipts are exempt, later ones consume the allowance. That rests on an unenforced rule that result callbacks never authorize work. My answer: there is one launch call site, the F7 comment marks the dependency, and tests 1, 3 and 4 pin both sides. Any alternative (a phase flag or a separate fence) adds state for no current path. A broader objection is that C1 will likely show nothing (see the first observation below). That doesn't change the predeclared arm.

## Smallest disposition

Adopt the relocation as proposed. Fix F1 (one clause). Add the F7 comment clause. Fold F2–F5 into the test specifications. Apply F6 to the plan body and lesson, and F8 to the wording.

## Observations (not actionable this pass)

- **Prediction:** PostgreSQL receipts committed in their launching turn in the baseline, so the relocation probably changes nothing on PostgreSQL. On SQLite, same-turn dispatch also needs startup settlement to be visible within the receipt turn, because the startup window is one. The predeclared decision rule is unchanged.
- **Turn counts:** after an initial-phase receipt, `_resume_startup_dispatch` still notifies, which usually causes one follow-up turn after the same-turn dispatch. Expect lower latency and fewer receipt-only turns, not fewer total turns.
- **Trace fields:** besides the initial `worker_results` span, `process_launch` spans emitted inside initial-phase receipts (`:1831`, before `:1839`) also carry the previous turn's flag value. The harness doesn't read this field.
- **Pre-existing, not made worse by C1:** a launch-lane "error" event with no request of its own is attributed to whatever request is currently active (`:1938`). Separately, a late failure receipt with admission disabled has no retry deadline. That one is covered by the accepted F7 limit.

No reconsideration requested.

</details>

<details><summary>Verbatim final C1 closure review</summary>

## C1 initial-result reset relocation: scoped review

**Verdict: no blocker.** The design is correct and is the smallest one available. I have four wording findings and three test-precision findings, all non-blocking. F1 and F2 are worth applying before promotion and before writing the tests. Nothing needs RECONSIDERATION REQUESTED.

### Context reads

I read all of these in full and in order: `docs/program-theory.md`, `docs/agent-context/README.md`, `decision-hierarchy.md`, `principles.md`, `engineering-principles.md`, the runbooks `testing-patterns.md`, `review-loops-and-agent-bootstrap.md` and `hardening-plans.md`, `docs/agent-context/lessons.md`, and all of `docs/lessons.md` (watermark none).

I also read the governing text: [MF-6] `05:1352-1358`, [MA-1.8] `03:441-584`, MANAGER.11 `07:1069`, the MANAGER.18 mapping `07:1191-1194`, IMPL.8/IMPL.10 and [TS-0] `08:7-64`. In the plan I read `2026-10-22-bounded-concurrent-manager-launches.md:25-284`. In live code I read the Manager, BaseTask and ServiceTask owners named below.

### Identifiers and causal premise checked in live code

- **Reset location.** The reset is at `manager.py:8366`, before the initial drain at `:8370`. The startup-handoff loop is at `:8371-8378`, stale recovery at `:8379` and the control drain at `:8380`. So the proposed slot (after `:8370`, before `:8371`) still sits ahead of every control, leadership, cleanup, convergence and delivery phase.
- **Who sets the flag.** Only `_launch_child_task` (`:1481`) and `_handle_child_launch_result` (`:1839`). Nothing reads it inside the initial drain's callbacks. Its only readers are `:5183`, `:8423`, `:8597`, `:8609`, plus the trace field at `:878`. Moving the reset therefore changes behaviour only at the phase boundary.
- **Initial-phase callbacks.** `BaseTask._drain_worker_results` (`base.py:863`) feeds `Manager._handle_worker_result` (`:1966`). From there a result goes to ServiceTask (`service.py:609`), then `_handle_service_worker_event` (`:1929`), then `_handle_child_launch_result` or the ServiceTask no-op (`service.py:526`).
  - The child-launch lane is the only lane the Manager registers (`:602`).
  - `_launch_child_task` has one caller, `_handle_work_message` (`:5566`). That handler is reachable only through the reserve-queue configs (`:1170-1182`).
  - The success, failure and custody paths (`_commit_child_launch_success`, `_handle_child_launch_failure`, `_reconcile_launch_now` → `_advance_launch_custody`, `_resume_startup_dispatch`) select nothing, reserve nothing and authorize nothing.
  - **Conclusion:** the initial phase cannot authorize a launch, so the moved reset cannot erase an authorization.
- **Late receipts are a real case.** `_has_worker_activity` (`base.py:822-824`) makes two separate observations. The worker thread publishes its result and then removes itself from the thread set in `finally` (`base.py:759-768`). Between those two observations, `_retry_stale_child_launches` (`:1281`) can see "no activity" while the result is already queued. The ordinary branch's BaseTask pre-pass drain (`base.py:1488`) then applies that receipt with no active-map entry. `_child_launch_already_committed` (`:1663`) separately handles same-process replay. Both paths still go through `:1839`, so keeping that assignment is required. This confirms rejecting F1's deletion.
- **Every reservation is still gated.** All spawn reservations pass through Manager `_process_queue_message` (`:5181-5186`), whether they come from the BaseTask priority pass, the explicit public drain or the internal drain inside convergence. Both handler fences (`:5489`, `:5557`) are unchanged.
- **The May lesson's reason still holds.** That lesson (2026-05-15) says "do not accept another spawn request before control has had a chance to run". `_drain_control_queue_first` still runs before any reservation in the turn. Only the lesson's first sentence ("still counts as launch work for that turn") stops being true.

### Findings

| ID | Sev | Location | Finding | Suggested disposition |
|---|---|---|---|---|
| F1 | P2 (wording) | Proposed [MA-1.8] clause | "Settlement or reactivation before that turn's dispatch point, including a receipt…" also covers late receipts that land before a dispatch point: the BaseTask pre-pass (`base.py:1488`) and the active-launch-branch drain (`:8417`). Read literally, it says those receipts may permit dispatch, which contradicts the new [MF-6] sentence and the code. | Make receipts qualify only in the initial phase: "*Startup settlement before that turn's dispatch, or a receipt handled in its initial bounded result phase, may permit that turn's ordinary dispatch; later receipts and later settlement retain an immediate continuation turn without waiting for inactive-source rediscovery.*" Also state that the clause replaces exactly the second sentence of `03:471-476`, keeping "with room remaining in the startup window", the empty-source idling sentence and the blocked-backlog suppression sentence. |
| F2 | P2 (test) | Late-STOP and internal-priority keepers | When the `:1839` assignment is removed (the mutant), other gates can still block the reservation, so the test would pass vacuously. Gates that can do this: (a) the SQLite startup limit of 1 while the just-committed public child is still in `_startup_feedback` (`:1856`); (b) `_scheduler_undurable` (`:5191`); (c) `_admission_blocked_lanes` and the retry deadline; (d) the maintenance yield in the cleanup scope (`:5196-5203`); (e) public inbox membership in `_active_queues` (`:8424-8428`); (f) in the active-launch branch, internal work is reserved only through the throttled `_run_managed_service_convergence` (`:7955-7978`). | Assert gates (a)–(e) inside the `_process_queue_message` wrapper, in both the production run and the mutant run. Put the internal-priority witness in the same-process replay keeper. In the ordinary branch the BaseTask priority pass reaches the internal inbox directly (priority 0), so the throttle in (f) doesn't apply. Keep the public late-STOP witness on PostgreSQL, with the startup window shown open at the decision point. Otherwise, use an internal witness only where convergence is shown to be due. |
| F3 | P3 (test) | Early-STOP mutant | Control (`ctrl_in`) gets the default priority, while the internal inbox is `QUEUE_PRIORITY_INTERNAL=0` (`_constants.py:984`). With the turn-start control drain removed, an internal successor is reserved *before* STOP is seen, and then the handler fence releases it (`spawn_reserved_released`, `control_disallowed_launch_before_validation`). A public successor at the same priority as control is order-dependent. | Use an internal successor as the witness. Record reservations at handler entry or through the shared observer, not via child count or the final source state, because the release puts the row back. Pair this with a positive assertion that STOP was processed in the same turn. As stated, this test protects existing ordering and does not distinguish B from C1. |
| F4 | P3 (coupling marker) | `manager.py:1839` | After the move, this assignment matters only for late receipts; the turn reset overrides it for initial ones. Without a note, it looks dead for the common case, which invites the same deletion F1 proposed. The "callbacks never authorize" reliance is also documented only at the reset, not where a future editor would break it. | Add a one-line comment at `:1839`: late receipts close the turn's allowance, and this handler must never authorize because the reset after the initial drain depends on that. This keeps the coupling marked at both edit points (engineering principle 11, floor 1). |
| F5 | P3 (comment) | `_resume_startup_dispatch` `:1863-1864` | This function is also called on settlement (`_release_startup_feedback`, `:1873`), not only on receipts. For initial-phase receipts the wake is redundant, because any dispatch already notifies (`:8630`, `multiqueue_watcher.py:1588`). | Comment: "*A receipt or settlement after this turn's dispatch point may find its original wake consumed; reactivated work needs its own local wake.*" Keep the wake unconditional; no phase state is needed. |
| F6 | P3 (plan/spec text) | Plan `:52`, `:181`, `:209`, `:213-215`, `:247`; `07:1194`; May lesson | These still say every receipt is exempt ("Receipt does not count as a new authorization", "Applying a prior launch receipt does not consume…"). `07:1194` says "next-turn reactivation". If the disposition is recorded but these lines are left as they are, the fix is unapplied. | Narrow each to the initial-phase form at promotion. For the May lesson, edit in place with a marker: "*(revised 2026-10-28; was: a start-of-turn receipt consumes the turn's launch allowance) Receipts applied in the initial bounded result phase settle earlier work before the allowance opens; the turn-start control drain still precedes any reservation, so a fast launch worker cannot recreate the same-turn STOP race. Later receipts still close the allowance.*" Update `07:1194` in the code slice (strategy A). |
| F7 | nit | Test harness choice | These are exact-turn-ordering tests. Under [TS-0] they must call `process_once` directly, not `drive_until`. For the absence and exactly-one assertions, the producer-closure boundary is the return of the single owned turn, since the reactor is the only thing that reserves. | Say this in the test plan. |

### The four questions

- **Correct?** Yes. [MF-6] as proposed matches the code sentence by sentence:
  - The active-request gate, at most one launch per turn (`:1479-1481`), and the initial-phase settlement all match.
  - The gate order matches: startup handoff, stale recovery, control, leadership, cleanup, then the reservation gates.
  - Late receipts close the allowance, and the final pre-seed fence (`:5557`) is unchanged.
  - "At most one new child launch" is only an upper bound. Pre-seed failures aren't launches, so the sentence doesn't claim C2's failed-attempt accounting.
  - Custody is unchanged: an undurable post-launch checkpoint still blocks ordinary dispatch at `:5191`, and MANAGER.11 is untouched.
  - [MA-1.8] is correct once F1 is applied.
- **Elegant?** Yes. It moves one existing line and adds comments: no new state, no extra broker read, no new API. The trade-off is that the flag now means "launch submitted, or late receipt seen". That is acceptable once it is commented at both edit points (F4).
- **Reuses existing primitives?** Yes. It uses the existing flag, gates, fences, wake, and the replay de-duplication in `_child_launch_already_committed`. No receipt-fence state is needed.
- **Will the tests falsify the claimed boundaries?**
  - **Progress test (RED).** It fails when the reset goes back to its old position, provided the receipt is fully queued before the turn, observed inside the initial `worker_results` phase, and the startup window is shown open. On SQLite with a public prior child that requires visible settlement; otherwise use PostgreSQL.
  - **"STOP before reservation".** This is falsified only if the test observes reservations at handler entry. Child count can't tell: the fences make "no child" true in both the production and mutant runs (F3).
  - **"Internal priority after a late receipt".** Falsified only on a path where the mutant actually attempts a reservation (F2).
  - **Two eligible successors.** This proves exactly one launch only if the test also asserts that the second exact ID is still in its source queue when the turn ends and is launched in a later turn.

### Strongest counterargument

The exemption rests on a convention the code doesn't enforce: result callbacks never authorize. A later change that authorized work inside the initial drain would have its flag silently cleared by the reset, allowing a second launch in that turn. Splitting the flag in two or adding a fence at the reservation point would make this robust, but that is new state or a broader fence, which was already declined. The smaller mitigation is F4's marker at both edit points plus the two-successor keeper. I agree with not expanding.

### Observations (not actionable in this pass)

- A launch-lane `error` event that arrives when the active map is empty is ignored without setting the flag (`:1937-1940`). This predates C1 and has no effect, since nothing is committed.
- An internal successor can now be reserved in the same turn as an initial receipt whose public post-launch checkpoint failed. B allowed the same thing one turn later; [MA-1.8] gates only ordinary dispatch on that checkpoint. The checkpoint-custody keeper's statement is correct.
- On a replay, `_clear_active_child_launch` clears the admission retry (`:1276`). This predates C1.
- The plan's trace caveat is accurate: the `worker_results` span is emitted before the moved reset, and the receipt-only metric doesn't read that field (`test_manager_dispatch_costs.py:1112-1117`).

No files were modified and no tests were run.

</details>

C1 text-first promotion applied in `/Users/van/Developer/weft` against HEAD
`63dd4752fdff1e3a1da42187d881d0f1942565ce`, before runtime edits.
Comprehension: a free lane alone is not authority; every normal resource,
startup, ownership, custody and control gate remains. A serial pass never waits
for a receipt. Missing process evidence never authorizes reseeding. B's yield
returns settlement to its existing owner. C1's reset is safe only because its
initial callbacks cannot authorize; late receipts retain the turn-closing flag.
The observed B-before measurement checkout remains unchanged at the same HEAD.

C1 test qualification: initial SQLite execution exposed wrong enclosing phase
expectation and manual-turn setups lacking the normal readiness precheck. Those
were corrected using the actual `_has_pending_messages` owner before the
asserted turn/restored liveness; no gate state was set. Final SQLite B run
(`c1-final-red-sqlite.log`) has two intended progression failures and four keeper
passes (3.68 s). PostgreSQL initially produced the same two intended REDs but
a fixture cleanup failure, so that run is not a clean keeper gate.

The cleanup failure reported an uncertain survivor with no surviving managed
PIDs. A real subprocess probe through `terminate_process_tree` confirmed psutil
reaped the process while its multiprocessing handle retained `exitcode=None`
and `is_alive=True`; timed joins then returned immediately. Evidence:
`reap-owner-probe.py` and `reap-owner-probe.json` in the execution directory.
The fixture now closes its actual worker threads, joins children through their
Process owners, and finally runs Manager cleanup. This preserves physical
producer closure without competing reapers or a longer budget. It does not fix
the pre-existing production cleanup diagnostic; that distinct owner collision
remains a confirmed observation for explicit disposition.

Qualified PostgreSQL B RED completed: two intended progression assertions fail,
four control/custody/replay keepers pass in 7.75 s, after closing fixture reaping
ownership (`c1-qualified-red-postgres.log`). SQLite earlier qualified two REDs
and four keepers remain the pre-fix proof; final cleanup-order change gets GREEN
verification on both backends. Runtime edit now moves only the reset after the
initial drain and adds the reviewed coupling/wake comments. No new fence,
state, broker read, worker, loop, or changed drain budget was added.

Fresh C1 B-before baseline at63dd4752 is complete: four quiet/trace ×
SQLite/PostgreSQL batches, warmup plus three samples each, 568 independent raw,
source-provenance, config, control and custody checks passed. Corrected harness
SHA256 `e581597e4ad92df7c0bcb6232a976219a7cd342ba3013b53998654bf75484130`;
results remain separate from the earlier A/B/A harness revision. Raw baseline:
`dispatch-measurements/C1-B-before/`; verifier `verify-c1-baseline.py`; derived
proof `C1-B-before-verified.json` in the execution directory. Values in seconds:

| Backend/mode | Useful elapsed | PING call-to-return | STOP handler return | Receipt-only turns |
|---|---|---|---|---|
| sqlite/quiet | [4.297253833, 4.36699075, 4.314964875] | [0.012259542, 0.015668416, 0.012934625] | [0.012321625, 0.002982208, 0.00268725] | [None, None, None] |
| sqlite/trace | [4.059260417, 3.915023958, 4.107326375] | [0.014435208, 0.018684917, 0.013934208] | [0.003674208, 0.0051205, 0.0041225] | [24, 24, 24] |
| postgres/quiet | [5.281058667, 5.484970083, 5.168253083] | [0.182524083, 0.1839685, 0.171176125] | [0.079221542, 0.0462115, 0.070456417] | [None, None, None] |
| postgres/trace | [7.570949125, 12.515982417, 11.320626542] | [0.204551875, 0.192513709, 0.242457375] | [0.067129917, 0.046938458, 0.091808125] | [0, 0, 0] |

All trace receipt censor counts are zero. PostgreSQL structural baseline remains
zero, so C1 cannot satisfy the strict receipt-turn improvement criterion there;
it needs the already-declared quiet elapsed benefit. Larger trace elapsed
spread is retained, not discarded or treated as a primary speed series.

C1 current owner module GREEN: SQLite31passed in21.34s and PostgreSQL31passed
in25.70s. An earlier candidate GREEN attempt exposed fixture teardown requesting
STOP before joining in-flight workers; that invokes the worker's late-result
self-reaper and the same competing-reaper diagnostic. Final teardown first
releases gates and joins workers and Process handles, then requests STOP and
always runs Manager cleanup. The phase/reservation assertions precede teardown.
This is fixture ownership correction, not runtime cleanup mutation.

Mutation qualification exposed a further early-STOP setup weakness: removing
the initial control drain initially failed only the eventual STOP assertion,
not a reservation assertion, because the internal source was inactive. The
final case performs the same real pending-message precheck used elsewhere.
With that source reachable, the mutation repeatedly reserved/requeued inside
the priority pass rather than returning. The diagnostic run was interrupted
after64.49s; all processes in its owned process group retired. It is not a
qualified sensitivity result. The final observer now raises immediately after
a real forbidden initial-STOP reservation, preventing a broken guard from
hiding its first causal violation behind a loop. On the real candidate no such
reservation occurs and the complete-turn absence/STOP checks still run. The
final SQLite mutation fails that exact assertion in2.77s; PostgreSQL and
restored positive controls follow. This is a test failure boundary, not a
production loop or timeout change.

### C1 implementation review dispositions, 2026-10-28

Contained Tier-1 Claude review completed in464.06s. Runtime: no blocker, correct,
elegant, appropriate primitive reuse. It reported F1 as a test-proof blocker
under the assumption of a single-site undurable mutation. Root checked that
claim against the actual two-site mutation and executed logs before disposition.

| ID | Disposition |
|---|---|
| F1 | Modify. The falsifier already removed both readiness and reservation terms, as `admission-c1-mutations.json` shows. Both real backends failed `failed checkpoint opened ordinary reservation`, with actual exact-ID fetches. Clarify this two-site contract in operative prose and cite the existing direct single-site keeper. Decline the extra gate observer: the executed reservation itself proves every preceding gate was passed, and the failure is already the intended custody assertion, not an exception or admission denial. The reviewer did not receive these then-pending results. |
| F2 | Accept, already executed. The early-STOP case now uses the actual readiness precheck and fails at the first forbidden successful fetch. Initial v1 failed only eventual STOP; v2 exposed the broken priority-loop cycle and was interrupted; v3 fails the exact reservation assertion on SQLite2.77s and PostgreSQL6.63s. |
| F3 | Accept. Correct [MANAGER.18] mapping to cite allowance closure [MF-6] and continuation [MA-1.8]; add [MA-1.8] to the owning turn docstring. |
| F4 | Accept. Actual receipt-enqueue plus observed initial phase replaces unnecessary thread-retirement qualification. Late STOP uses internal prior child and public successor on both backends, avoiding startup-slot masking. Operative plan updated. |
| F5 | Accept. Remove the extra malformed reserved-policy assertion; retain real malformed reservation followed by valid authorization, which proves the allowance counts launches rather than rejected rows. |
| F6 | Accept. STOP docstring now names early and late roles. |
| F7 | Accept. Rewrap touched [MA-1.8] paragraph. |

Eight qualified mutation executions produced12intended failures across both
backends (`c1-mutant-verified.json`): old reset2/backend, removed late receipt
flag2/backend, deleted initial control drain1/backend, both ordinary custody
gates removed1/backend. Source snapshots remain under `c1-mutants/`. The full
review was supplied before these completed; its statements that GREEN/mutations
had not happened are temporal limits, not current verification status. Its
cleanup observation also predates final teardown ordering: workers and Process
handles now close before test-requested STOP, avoiding late-result self-reaping.

Author fresh-eyes is retained in `c1-author-fresh-eyes.md`; final closure review
gets the exact current delta and executed assertion logs. No measurement or
retention outcome is inferred from correctness.

<details><summary>C1 implementation review verbatim</summary>

I couldn't write the plan file or call ExitPlanMode because neither tool is available in this session, so the full review is below. I made no edits and ran no tests or shell commands. I also couldn't hash anything, so the B HEAD and the Manager SHA-256 are unverified.

# C1 review: initial-result-only allowance reset (delta against `63dd4752`)

**Verdict: blocker F1, which affects only the test proof.** The runtime change has no blocker. The checkpoint-custody keeper's declared mutant ("remove the ordinary `_scheduler_undurable` gate") will probably survive. A second gate on the same path stops the reservation first, so the test passes without ever exercising the removed gate.

## Live-code checks

- **Reset and assignment.** The reset now sits right after the initial drain (`weft/core/manager.py:8371-8374`) and still comes before startup handoff (`:8375`), stale recovery (`:8383`) and the control drain (`:8384`). The assignment is kept at `:1841`. The flag is set only at `:1481` and `:1841`, and is read only at `:5185`, `:8427`, `:8601`, `:8613` and the trace field at `:878`.
- **Who can call dispatch.** The only callers of `_run_managed_service_convergence` and the spawn drains are inside the turn (`:8405`, `:8449`). So no caller outside a turn sees the flag change meaning.
  - One effect is new: the forced convergence at `:8405` can now drain internal work in the same turn as an initial receipt. That still happens after the control drain, as [MF-6] requires.
- **Initial callbacks don't authorize.** I re-checked the result routes (`:1931-1982`, `service.py:609`, `:1759-1847`). None of them reserves or launches anything.
  - If the initial drain raises, the reset is skipped and the turn ends. That only makes the next turn more conservative.
- **Late receipts are real.**
  - `BaseTask._process_reactor_turn` (`base.py:1488`) applies receipts that arrive in its pre-pass.
  - The active-branch drain (`:8421`) does the same.
  - The worker publishes the result before the thread is discarded (`base.py:759-768`, `service.py:575-585`), so stale recovery (`:1281`) can race with a queued result.
- **Precheck owner.** `_has_pending_messages` (`:5046`) is the real wait-path precheck. It only marks broad rediscovery (`multiqueue_watcher.py:897`, `:1339-1351`) and sets no gate. The other source of rediscovery is the 1.0 s interval (`TASK_INACTIVE_QUEUE_DISCOVERY_INTERVAL_SECONDS`).
- **Constants.**
  - The result budget is 64, so started, result and stopped events all fit in the initial phase.
  - W=6. Internal priority is 0 and normal priority is 100.

## Findings

| ID | Sev | Location | Finding | Suggested disposition |
|---|---|---|---|---|
| **F1** | **P2, blocker (test proof)** | `test_receipt_checkpoint_failure_retains_exact_custody`; gates at `manager.py:5036` and `:5193` | **The undurable-gate mutant is masked by a second gate, so it survives without failing any assertion.** The ordinary undurable gate exists twice: in readiness (`_queue_has_pending`, `:5032-5038`) and in reservation (`_process_queue_message`, `:5193`). In the faulted turn, `_resume_startup_dispatch` marks the public source active. `_drain_queue` then calls `_update_active_queues` (`multiqueue_watcher.py:1574`, `:1332-1336`), which drops it through the `:5036` gate. The explicit fallback also needs the source to be active (`:8429-8432`), and its drain refreshes the active set first (`:8597`). So in production `_process_queue_message(public)` is never reached; the test's own comment ("The failed-custody turn made public work inactive") confirms this. Removing only `:5193` takes the same path, finds `reservations == []`, and passes. Removing only `:5036` is caught by `:5193`. Only removing both reaches a reservation. | **Modify.** Declare the falsifier as removing the undurable term at both `:5036` and `:5193`. Add a pass-through observer on `_process_queue_message(public)`, using the same pattern as `active_branch`. In the mutant run it should assert that every other gate is open: no startup feedback, evidence available, the candidate is `next_id`, admission is not blocked, and no child maintenance is due. That makes the failure the `reservations == []` assertion, not an admission denial or an exception. In the production run, assert `source.name not in manager._active_queues` after the faulted turn, to show the readiness half did the work. The single-site `:5193` mutant is already killed directly by `test_undurable_internal_launch_blocks_ordinary_reservation_until_checkpoint` (`tests/core/test_manager_family_maintenance.py:197-251`); cite it rather than claim it again. Also confirm the mutant's admission path doesn't write to the faulted `weft.state.tasks.<tid>` queue. |
| F2 | P3 (test) | Early-STOP case (`initial`) of `test_receipt_continuation_services_stop_before_reservation` | **The control-drain mutant always fails, but not always on the reservation assertion.** After the helper's first turn, the internal source is inactive: it was emptied, and `multiqueue_watcher.py:1441` added it to the inactive candidates. Whether the next turn rediscovers it depends on the 1.0 s probe interval (`:1340`). If rediscovery isn't due, the mutant reserves nothing and handles no STOP, so it fails on "queued STOP was not serviced" instead of on the reservation. That doesn't prove the ordering claim ("must fail even if a later handler fence requeues it"). | **Modify.** Call `assert manager._has_pending_messages()` before the turn, as the priority and replay tests already do. The positive run is unchanged because STOP returns at `:8384`. The mutant then deterministically reserves internal (priority 0) before ctrl_in. Record in the mutant log which assertion failed. |
| F3 | P3 (spec mapping) | `07-System_Invariants.md:1195-1197`; `manager.py:8358-8365` | The mapping cites "immediate-turn continuation under [MF-6]", but the continuation rule is in [MA-1.8]. [MF-6] covers only the reservation prohibition. Under strategy A, the newly mapped `_process_manager_reactor_turn` docstring should cite [MA-1.8] back. | Change to "…later receipts close it under [MF-6] and retain immediate-turn continuation under [MA-1.8]". Add [MA-1.8] to the turn docstring's Spec list. |
| F4 | P3 (plan text) | C1 test design versus the operative plan | Two deviations should be recorded as dispositions rather than left unexplained:<br>(a) The plan requires "no active worker thread and a pending result". The tests instead wait on the actual enqueue (`receipt_queued`) and pin `receipt_phases == [("turn","worker_results")]`. That is equal or better evidence, because thread liveness doesn't affect FIFO initial-phase handling within the 64 budget.<br>(b) The plan places late STOP on "public PostgreSQL". The test uses an internal prior child, which takes startup gating out of play, and runs on both backends. | Accept both. Update the C1 record with the reasons. |
| F5 | nit (scope) | Progression test, invalid row | The `"{"` row checks that a rejected delivery doesn't close the allowance, which is in scope ("new child launch, not attempts"). The assertions that it stays in `reserved` (`== invalid_body`) pin the existing malformed-request reserved policy, which isn't C1's concern. | Keep the reservation-order assertions. Drop the reserved-disposition assertions, unless the owner wants that policy pinned here. |
| F6 | nit | STOP test docstring | "A late receipt keeps dispatch closed…" describes only `active_branch`. `initial` is the early-STOP ordering keeper, not a C1 witness. | Make the docstring and ids state both roles. |
| F7 | nit | `03-Manager_Architecture.md:471-473` | The replacement is one long line in the middle of a hard-wrapped paragraph. | Rewrap, or leave as is (the spec mixes both styles). |

## Answers

1. **Correct?** Yes. The moved reset keeps control-first ordering (every reservation path still runs after `:8384`), internal priority, fresh admission, startup gating and custody. Late receipts close the allowance exactly as in B.
   - The new [MF-6] and [MA-1.8] text matches the code, with one caveat. "At most one new child launch" relies on `_launch_child_task` having its only call site in `_handle_work_message`, where every path is gated by the flag.
   - The lesson's revision marker and its kept reason are accurate.
2. **Elegant expert systems programming?** Yes. It moves one line and adds comments at both edit points. It adds no state, broker read, API, loop or change to the drain budget. The cost is that the flag now means "launch submitted or late receipt seen". That is acceptable because both edit points are marked.
3. **Existing primitives reused without overcomplication?** In the runtime, yes. In the tests, mostly:
   - They reuse `startup_pg_manager`, `_publish_waiting_task`, `_join_waiting_processes`, `_dispatch_witness_body`, the real `launch_task_process` and the local result channel.
   - They read gates through `_assert_receipt_dispatch_ready` without changing them.
   - The internal-priority RED gives a second witness for the same old-reset mutant. It is cheap given the shared helper, and it covers the "each free-lane selection keeps priority" row, so I'd keep it.
   - Unnecessary work: F5 only.
4. **Falsifiability and producer closure.** Every exact-turn assertion is closed by the return of a direct `process_once` call. The `drive_until` in the progression test runs only after the exact-turn proof, as a liveness check. Expected mutant outcomes:

| Mutant | Expected result |
|---|---|
| Old reset placement | Progression fails on the authorization list. Priority fails on "ready receipt did not permit…". Both match the supplied RED evidence. |
| Delete the receipt assignment at `:1841` | `active_branch` fails at `reservations == []`, with `late_gate_calls` non-empty in the message, so check the log for that. Replay fails at `reservations == []` after `gate_calls[0] == internal`. Both observers assert open gates at the decision point. Admission is only shown open by the reservation itself; this is acceptable but should be confirmed in the log. |
| Delete the initial control drain | Fails, but on the reservation assertion only with F2 applied. |
| Remove the ordinary undurable gate | Survives as a single-site mutant (F1). |

## Observations (no action needed)

- **Checkpoint keeper recovery.** Recovery is driven by the test (`_publish_scheduler_checkpoint()` and then the precheck), not by the Manager's own dirty-checkpoint retry in `_refresh_scheduler_evidence` (`:2511`). This is fine for C1's custody claim, but don't cite it as proof of wake or progress after recovery.
- **Failure receipts in the initial phase.** A failure receipt applied in the initial phase now opens the same turn. With admission enabled, `_schedule_admission_retry_for_source` (`:1777`) still gates retries. With admission disabled, the existing "no retry deadline" limit means a persistent failure can now retry up to twice as fast (one turn instead of two). That is not worse in kind and is already an accepted limit.
- **Fixture cleanup order.** The order (stop, join workers, join Process handles, then `cleanup`) is sound. The worker lanes stop only in `_stop_worker_lanes` during cleanup, so the held publish cannot be refused during teardown.
- **Duplicate gate.** The duplicated undurable gate (`:5036` and `:5193`) predates C1. In reachable paths the reservation-side check is defense in depth.
- **Unverified run claims.** The GREEN run on both backends and the mutant runs haven't happened, and I didn't run them. SQLite progression GREEN depends on the first child's startup settling inside the receipt turn; the helper waits for `published`, so that should hold.

No reconsideration requested. **Blocker: F1** (modify the mutant definition and add the observer). F2–F7 are non-blocking.

</details>

### C1 implementation closure and original-load check, 2026-10-28

Contained cross-family closure completed in89.68s: no blocker, F1–F7 closed. Reviewer explicitly corrected its single-site mutation assumption. Author accepts all closure dispositions; optional duplicate source-absence check is retained as harmless exact queue evidence, with no expanded gate test. Full SQLite:5982passed38skipped232.37s. Full PostgreSQL stopped at4751passed29skipped with one test setup failure, not a completed pass.

The startup-pressure test prequeued three requests, then observed two receipts at turn return before installing pressure. C1 can receive receipt2 in its initial phase and authorize3 before returning. Full-suite diagnostics show all three spawned; a focused pre-pressure exact-source assertion failed `third request dispatched before pressure installation` in8.42s. The correction queues only two initially, acknowledges real external pressure, then adds/registers the third before the next turn. It changes neither workload size, pressure, assertions nor deadlines. Positive PG7.06s; startup-window bypass mutant fails intended blocked-state assertion after30s/960turns; restored positive6.70s. Root fresh-eyes: the owner-thread queue insertion closes the setup race without replacing admission or assuming receipt consumes a turn. New-head full PostgreSQL and independent test review remain gates.

One test review was stopped without verdict because its prompt captured a temporary mutation during orchestration. It is invalid evidence; a fresh clean-source review was dispatched. Future temporary mutants belong in isolated snapshots when readers are active.

<details><summary>C1 implementation closure verbatim</summary>

**Verdict: no blocker.** C1 closure findings F1–F7 are resolved at `63dd4752` plus the current delta. I only read files. I made no edits and ran no tests. I don't have a write tool or ExitPlanMode in this session, so the review is here rather than in the plan file. The mutation logs are under `/tmp`, outside the repo, so I took their contents and hashes from the evidence you supplied and did not re-read them.

## Findings and suggested dispositions

| ID | Checked against | Suggested disposition |
|---|---|---|
| **F1** | `admission-c1-mutations.json` removes both `or self._scheduler_undurable` terms: readiness at `weft/core/manager.py:5036` and reservation at `:5193`. Both backends failed the intended assertion, `failed checkpoint opened ordinary reservation`. The plan now states the two-site falsifier (`docs/plans/2026-10-22-bounded-concurrent-manager-launches.md:189`). It also cites the direct keeper for the reservation-side gate alone: `tests/core/test_manager_family_maintenance.py:197` calls `_process_queue_message` directly, which reaches `:5193` without passing through readiness. | **Close; agree with Modify, and agree to decline the observer.** My earlier objection assumed only one gate had been removed, and that was wrong. A public reservation can only happen if every earlier check in `_process_queue_message` passed: the busy lane, startup, evidence, candidate, maintenance and admission. So the reservation itself proves the gates were open, and an observer would add nothing. The failure message is the custody assertion, not an exception, so the admission path did not write to the faulted state queue in a way that raised. |
| **F2** | In the `initial` case, the test now calls `_has_pending_messages()` before releasing the receipt. The `fetch` observer raises at the first forbidden internal reservation. The positive run returns from the drain branch at `:8385`, before any dispatch. Nothing wraps `_fetch_next_message` at `multiqueue_watcher.py:1413`, so the assertion propagates. Even if something swallowed it, `reservations` was already appended, so the end-of-turn `reservations == []` check would still fail. | **Close.** The failure now proves early STOP comes before reservation, not just that STOP eventually happens. |
| **F3** | The mapping at `07-System_Invariants.md:1194-1197` now cites closure under [MF-6] and continuation under [MA-1.8]. The turn docstring at `manager.py:8363` lists [MA-1.8]. | **Close.** |
| **F4** | The plan at `:185` now relies on the actual receipt enqueue and the observed `worker_results` phase, and explicitly says worker retirement isn't needed. The late-STOP case uses `internal=True` for the prior child and the public `inbox` as successor, on both backends. | **Close.** Thread liveness doesn't matter to the FIFO proof within the result budget. |
| **F5** | The reserved-disposition assertions for the malformed row are gone. What remains: the malformed row is reserved before the valid one, only the valid one is authorized, and the third request is deferred. | **Close.** |
| **F6** | The docstring now reads: "Early STOP precedes reservation; a late receipt defers it until controls." | **Close.** |
| **F7** | The paragraph at `03-Manager_Architecture.md:471-479` is hard-wrapped now. | **Close.** Its lines run to about 86 columns while nearby text wraps near 80. That's cosmetic and the spec already mixes widths. No action needed. |

On the runtime: the delta still only moves the reset after the initial result drain (`:8371-8374`) and adds comments. That matches what the earlier review called correct and elegant. Nothing in the F1–F7 changes touches runtime logic.

On the teardown reordering: the fixture now releases gates, joins workers, and joins the Process handles (escalating if needed) before `stop` and `cleanup`. That fixes the late-result self-reaping issue noted in the earlier observations. It's fixture-only and doesn't weaken any runtime timeout or cleanup path.

## Observations (no action needed)

- **A mutant that removes only the readiness gate (`:5036`) still survives every test.** The reservation-side gate at `:5193` masks it, and the direct keeper only checks `_queue_has_pending` positively after recovery (`:251`). The duplicated gate predates C1 and C1 doesn't make this worse. It's out of scope, but could go to a future custody-keeper pass if anyone wants each half pinned separately.
- **One leftover F5 check is redundant but harmless:** `source.peek_one(exact_timestamp=invalid_id) is None` (progression test) repeats what the fetch-recorded reservation order already proves. Keep it or drop it.
- **A teardown failure would be reported alongside the original test failure, not replace it.** If the teardown's `assert all(not … is_alive())` fails during unwinding, pytest chains it after the primary failure.
- **The plan's narrative and disposition table read differently on F2.** Prose at plan `:1857-1859` still says "PostgreSQL and restored positive controls follow", while the F2 row records PostgreSQL at 6.63s. Both are timestamped records, so this is fine as a historical log.
- **Gates still open, as you stated:** the full PostgreSQL run is in progress, and there is no measurement or retention claim yet. These were outside this review.

No reconsideration requested. **No blocker.**

</details>

### Startup-pressure setup review closure, 2026-10-28

Contained cross-family review completed in86.63s: no blocker; correctness, elegance and primitive reuse pass. F1 optional comment revision declined: current wording is accurate across both retained baseline and provisional C1, and this test should not require the provisional C1 contract. No runtime or test changes required by review. The reviewer's phrase “impossible to satisfy” applies to an initial-phase receipt interleaving, not all executions; its later analysis correctly identifies late receipts as the previously passing interleaving. Its frequency claim (“common”) was not measured and is not adopted. Restored positive PG passed6.70s. Full original-load run remains separately recorded.

<details><summary>Pressure-test review verbatim</summary>

I've read the test, the turn loop, the admission gate and the C1 review record. Here is the verdict.

## Verdict: no blocker

**Your premise is correct.** C1 makes the old test setup impossible to satisfy:

- **Where the flag resets.** `weft/core/manager.py:8371-8374` drains worker results first, then resets `_child_launch_started_this_turn = False`. A receipt for launch 2 handled in that first drain therefore no longer closes the turn's launch allowance. This is C1's intended rule, recorded in the bounded-launches plan at `:1743` and `:1902`.
- **What happens in that same turn.** `_resume_startup_dispatch` (`:1860-1867`) clears the retry and marks the inbox active. Ordinary dispatch then reaches `_process_queue_message` (`:5183-5212`). None of its gates blocks:
  - no launch is active and the flag is now False;
  - `_startup_dispatch_blocked()` is False, because 2 unfinished startups is below the window of 6 (`_constants.py:801`).
- **Why the window lets launch 3 through.** `_admission_allows_queue` takes a fresh usage reading (`:2232`). `_startup_window_allows_launch` (`admission.py:40-44`) allows the launch while usage is below the threshold, and no pressure exists yet. So request 3 is reserved and launched before `drive_until(receipts(2))` returns (`reactor_driver.py:47-51`).
- **Why the old test then failed.** Nothing was left to block, so `_startup_blocked` never became True, and the test timed out at line 417. This matches the retained diagnostic (3 `task_spawned` before pressure) and your focused assertion.
- **Why it was intermittent.** A receipt that arrives late in the turn (`:8421` or `:8458`) still closes the allowance (`:1841`). In that case dispatch moved to the next turn, after the test had installed pressure. Which drain gets the receipt depends on when the worker thread finishes. Under C1, the early-drain case is the common one.

**The correction is sound:**

- **Ordering.** The owner thread runs no manager turn between the pressure acknowledgement and the write of request 3. The actor replies only after every raw connection has run `SELECT 1` (`:167-177`). That makes the order: real pressure installed, then request 3 written, then the next turn.
- **That next turn** classifies request 3 (`:8393`) before dispatch. It takes a fresh real reading at or above the threshold (and below `public_limit`). The window then denies the launch and sets `_startup_blocked` (`:2957-2966`).
- **Nothing was weakened.** Every assertion after line 422 is unchanged and still tests the same behaviour: no busy polling under pressure, PING still served, and a fresh reading below the threshold reopens dispatch with exactly three outputs.
- **Falsifiability.** Your mutation result (bypass the window predicate and the test times out at 30s / 960 turns) shows the blocked-startup check can still fail.
- **It works with or without C1.** If the measurement gate reverts C1, the receipt turn dispatches nothing. Request 3 still appears only after pressure, so the same path runs. The test no longer depends on the C1 decision, which is the right property while that decision is still being measured.

## Findings

| ID | Sev | Location | Finding | Suggested disposition |
|---|---|---|---|---|
| F1 | P3 (comment precision) | `test_measured_admission_pg.py:405-406` | "May also dispatch a third" makes the timing sound accidental. Under [MF-6]/[MA-1.8] as amended by C1, a receipt handled in the first result phase is *allowed* to permit that turn's dispatch. The test boundary exists because of that rule. | Optional rewording: "*An initial-phase receipt may permit that turn's dispatch [MF-6]; make the third request eligible only after real pressure is acknowledged.*" |

There are no correctness findings, and the change removes work: one fewer request sits queued during the pre-pressure turns.

## Answers

- **Correct?** Yes. The fix moves the eligibility boundary to the point the test always meant to observe. It mocks nothing, and capacity, deadlines, worker count and assertions are unchanged.
- **Elegant?** Yes. It is a three-line move of an existing write, with no new helper, event or wait.
- **Right primitives?** Yes. It uses a real queue write, the actor's existing acknowledgement as the ordering barrier, and the existing `register_tid` and `receipts()` helpers.

## Separate notes (not actionable, not blockers)

- **Lost witness.** This test no longer shows C1's positive behaviour (a receipt turn dispatching the next request while the window is open). Before the fix, it showed it by accident. That evidence should come from the C1 keeper tests, not from this test.
- **Existing gap in the startup check.** Only `ready[0]` is checked to be unset before pressure (`:401`, `:465`). Unfinished startups for children 1 and 2 rely on `publication` staying unset. This was already true and is not made worse.
- **Gate still open.** As you said, this verdict doesn't close the execution gate. The restored positive run and the full PG run are still the evidence that counts.

</details>

Original-load PostgreSQL rerun after the startup-pressure setup correction:5934passed29skipped in338.95s (`c1-full-postgres-pressure-fixed.log`). No worker count, deadline, pressure or runtime behavior was relaxed. This closes the local full-PG gate for the current combined working tree; corrected-head CI remains separate. Full SQLite was5982passed38skipped232.37s before the PG-only setup correction.

### C1 measurement decision and restoration, 2026-10-28

Rejected C1 after 12 valid batches / 96 scenarios and 1,894 verification checks. PostgreSQL quiet elapsed (seconds): B-before [5.281058667, 5.484970083, 5.168253083]; C1 [5.278710833, 4.867836917, 5.11627925]; B-after [4.843405791, 4.920405875, 4.782946084]. The speed bar fails, and surrounding baseline ranges are disjoint, limiting attribution. PostgreSQL receipt-only counts remain [0,0,0] in all arms with zero censoring, so the structural bar also fails. No primary quiet regression meets the predeclared all-three rule on either backend; this is not equivalence. No additional set will be collected to seek a win.

An unrelated Docker/Django pytest job began during the first trailing SQLite trace batch. The boundary guard stopped subsequent measurements; that entire batch is retained under `excluded-external-overlap/` and excluded. After the job ended, only that invalid batch was replaced and the two not-yet-started PostgreSQL baseline batches completed. Earlier candidate and baseline batches ended before the external job began. A resume preflight matched its own shell heredoc and stopped before any experiment; a separate invocation resolved it. Final read-only arithmetic required repository Python3.14 because system Python3.9 rejects `zip(strict=True)`; no data, script bytes, criteria or runtime samples changed for that correction. Exact state/provenance and analysis: `c1-measurement-run-state.json`, `C1-B-C1-B-analysis.json`, `C1-B-C1-B-verification-checks.json`, and raw `dispatch-measurements/` under the execution directory.

Restored B Manager, three specs, May lesson and original family-maintenance test file exactly from HEAD after verification; saved the complete candidate patch as `c1-final-working-tree.patch`. Kept only the independently reviewed pressure setup repair. Concurrent launch workers remain deferred.

### C2 simplification review dispositions, 2026-10-28

Initial contained cross-family C2 review completed in741.91s, BLOCKED. The operative design and proposed spec delta above now replace the broad first draft. No C2 runtime edits have occurred. The explicit amendment drops proposed aggregate result/control budgets and duplicate safety projections, retaining fixed pass structure, pre-seed attempted-authorization fencing, two real lane-work quotas and existing child maintenance. Other maintenance may be delayed by one bounded pass; this is a declared policy tradeoff, subject to control and timing gates.

## Review finding dispositions

| Finding | Disposition |
|---|---|
| F1 aggregate control starvation/churn | Accept. My earlier contrary suggestion that the draft had an exhausted-control **spawn** prefetch gate was incorrect: it explicitly gated ctrl-in fetch, not spawn fetch. Remove the aggregate quota and override; use boundary emptiness as above. Keep33 initial PINGs + exact no-release proof. |
| F2 exact-turn tests | Accept; inventory below. C1-only tests are removed with rejected C1. Real receipt timing must never be a hidden one-launch-per-turn premise. |
| F3 exact spec deltas | Accept. `c2-b-spec-replacements.json` gives exact B-baseline strings and replacements. Remove C1 first, then promote these, with reciprocal mappings. |
| F4 broad safety helper | Accept simplification. Do not add dead publication or empty-map stale checks, repeated child veto, service/timer projections. Retain B per-delivery child gate and classification continuation before pass2. Proposed C2 normative wording is amended explicitly, not silently ignored. |
| F5 structural budgets | Accept removal of result/control bookkeeping. Retain preseed attempted-authorization semantics using fixed2/1 passes plus flag, not a successful-launch-only limit. Retain both lane counters because Manager REQUEUE refills work. Reject the blanket dataclass taste claim: one small lifetime-owned record is clearer than independent nullable fields. |
| F6 first-pass equivalence | Accept correction of claim, not removal of required preseed bound. First-pass changes are explicit: lane attempt quotas and preseed failed-attempt fence. Source-hint retention is limited to successful ordinary submission. Trace code is unchanged. Measure the entire candidate; do not claim byte-identical first pass or unchanged SQLite cost. |
| F7 pressure phase | Accept need for exact phase; review's cited current test wraps usage observation but does not itself block as claimed. Selected new barrier described below. |
| F8 internal topology | The draft explicitly proposed publishing control **after** initial drain, so the statement that it was necessarily already consumed is too strong. Adopt simpler real eligible public topology anyway and forward-observe that priority draining actually ran. |
| F9 rejected C1 wording | Accept: remove exception and restore B; retire C1-only tests. |
| F10 opportunity evidence | Existing B trace reconstruction already proves72/72 receipts applied before the complete launching delivery pass returned. This proves the needed receipt opportunity, not capacity or speed; exact ready-at-start sampling is unnecessary. Count repeated existing `queue_dispatch` timing spans per complete (manager,turn) enclosure for opened extra passes. |

The two REQUEUE observations remain separate scope. Do not add changes to invalid-first selection or new error policy. Lane-attempt caps are part of this reviewed C2 fairness scope, and are removed if C2 is rejected unless separately authorized. Do not claim today's total limits are unchanged: public can do watcher1+explicit128 today, and internal priority draining can exceed its explicit helper's cap. The new bound is128 aggregate attempted fetches per lane.


<details><summary>Initial C2 plan review verbatim</summary>

# Review: C2 bounded serial batching amendment and source-resolution addendum

**Verdict: BLOCKED.** The fixes are narrow. Of the three items below, the first two block, and the third has to be done before any code is written. I made no edits and ran no tests. Every claim below comes from reading the code at the cited lines.

## Answers to your four questions

- **Can it be implemented confidently and correctly?** Not as written, because of findings 1 to 3. The core algorithm is sound, and I checked each piece:
  - Only four paths clear an active launch request: the stale retry before delivery (`manager.py:1291`), the launch exception (`:1494`), the receipt handler (`:1838`) and final cleanup (`:8081`). So "a launch was submitted in this pass and the active map is now empty" really does mean the receipt was handled.
  - Receipts still close the allowance (`:1841`).
  - Admission reads fresh usage on every call, with no caching (`:2222-2236`, `:2932-2967`).
  - Custody and startup gates run on every delivery (`:5189-5197`).
  - Not dropping blocked sources from the active list fixes a real priority inversion. The internal source gets dropped at `:5187` / `multiqueue_watcher.py:1416`. Without a wait in between, `_update_active_queues` only finds it again when a precheck or discovery is due (`:1338-1351`), and a receipt only reactivates the public source (`:1864`).
- **Does it hurt robustness?** Yes as written, through finding 1. With that fixed I found no other harm: one launch lane, no waits, per-delivery STOP fences, and receipts applied only after a pass returns.
- **Correctness:** The service-readiness extraction is behaviour-preserving. `ordinary_timeout<=0` matches `now-last>=interval_ns` exactly (`service.py:675-687`), and the reason strings match the current booleans (`:7804-7847`). The addendum's §2 custody conclusion also holds: [MA-1.8] is enforced per delivery through `_scheduler_undurable` (`:5193`) after `_reconcile_launch_now` (`:5638-5660`).
- **Elegance and use of existing primitives:** It is over-built for a budget of 2. Two existing primitives already do most of the work: the turn allowance flag (`_child_launch_started_this_turn`) and B's per-delivery gate. The five-field budget dataclass, two new overrides and three extracted projections are new concepts that cost more than they protect (findings 4 and 5).

**No RECONSIDERATION REQUESTED.** Nothing here requires reversing the program theory, a spec or an invariant.

## Blocking findings

**1. P1: The aggregate control allowance starves the first pass and churns reservations.**
- The budget covers the turn-start control drain (`:8384`, cap 32, `_constants.py:774`). A turn that starts with 32 or more control rows uses it all up there.
- Every first-pass spawn is then reserved and immediately released, because `_control_allows_child_launch` says no (`:5491` → `:5610`). The release requeues the row and re-indexes it (`:5425-5432`).
- Nothing closes the allowance flag on that path, so the explicit drain loop (`:8598-8615`) and the internal priority loop (`multiqueue_watcher.py:1522-1539`) keep re-reserving the same row until the 128 lane caps run out. That is up to about 512 broker writes in a turn that launches nothing.
- Baseline still launches one child per turn under the same control load, so this is a loss of capability.
- **Suggested fix:** leave the first pass's control handling exactly as it is today. Have `_drain_control_queue_first` return a bool that is true only when it reached an empty queue, and open the extra pass only if the boundary drain returned true.
- **Keeper test:** 33 PINGs queued at turn start; the first pass authorizes exactly one launch with zero releases. Applying the aggregate cap to the first pass must fail it.

**2. P1: Existing PostgreSQL exact-turn tests become dependent on receipt timing.**
- Two current tests assume nothing more is reserved in the turn:
  - `test_prior_launch_receipt_allows_one_new_authorization` asserts at `test_manager_family_maintenance.py:1694` that the third request stays in its source.
  - `test_receipt_continuation_preserves_internal_priority` asserts at `:1896` that the public request stays in its source.
- Under C2, if the successor's real receipt lands in BaseTask's post-pass drain (`base.py:1491-1495`), an extra pass opens and both assertions fail intermittently. This is the same kind of turn-counting premise you said C2 must not carry forward.
- **Suggested fix:** list every PostgreSQL test that asserts at most one reservation per `process_once` after a real launch. Hold the successor's receipt in a forwarding observer so no extra pass can open, and move the C2 claim into test family 1.
- For reference, the current `test_startup_pressure_…` already does it the right way: it writes the third request only after pressure is in place (`test_measured_admission_pg.py:402-409`).

**3. P2, required before code: C2 contradicts promoted spec text, and no exact replacement is given.**
- [MF-6] (`05-…:1356`) says "A turn permits at most one new child launch" and "A receipt applied after the initial result phase does not permit another spawn reservation for the rest of that turn."
- The second sentence of [MA-1.8] (`03-…:474-477`) and the [MANAGER.18] mapping (`07-…:1195-1197`) say the same thing.
- The C2 [MA-3] paragraph in the base plan still prescribes aggregate result and control budgets.
- Under promotion strategy A, the replacement text must be exact and reviewed before runtime edits. Write it only for whichever branch the C1 decision selects.

## Simplifications

**4. P2: Most of the safety predicate is dead or duplicated.**
- **Publication flag (dead):** `publication_pending` is set only in `_has_pending_observation` (`:1924`), which runs only on the wait path (`multiqueue_watcher.py:1149`, `:1201`). Every flag is cleared at turn start (`:8375-8382`), so it cannot be true between passes. The addendum's proposed spec sentence ("An already observed startup-publication hint … ends additional dispatch passes") describes a state that can't happen. Replace it with a plain statement: extra passes do not observe publications, and the startup window plus per-delivery admission bound the exposure.
- **Child terms (duplicated):** B's per-delivery sentinel/deadline gate (`:5198-5205`) stays active in pass 2 because the scope covers the whole slice. So family 7's "omit extra veto" mutant is equivalent and will survive. Test the B gate in pass 2 instead; the mutant is clearing the scope between passes.
- **Stale-launch timeouts (vacuous):** `_stale_child_launch_timeouts` returns `[]` whenever the active map is empty (`:8303`).
- **Service readiness, stale internal-reservation cleanup and the safety-timer extraction:** none of these guards a reservation invariant.
  - The tail still runs `_maybe_yield_leadership` (`:8445`) and service convergence (`:8448`) in the same outer turn, after at most one extra pass.
  - Leadership is advisory for reservation (`03-…:868`), and a proven yield already shows up as `_draining`.
  - What they buy is only ordering latency.
  - The extraction is correct, but I recommend not adopting it.
- **What to keep:**
  - stop, draining and termination;
  - an empty active map;
  - the boundary control drain from finding 1;
  - `_family_classification_pending`. This one has a real reason: unindexed newer rows may hold tip children that depth-first preference would choose (`03-…:445`).
  - Optionally, the existing pure `_admission_retry_timeouts`, unchanged.

**5. P2: Use the structural bound instead of counters where it is enough.**
- At budget 2, structure alone allows at most one extra pass: an extra pass needs a successful submission, and the second submission uses up the budget. Per-turn work is therefore at most twice today's per-call limits, with no aggregate counter.
- **Recommendation:**
  - Drop the result counter. That also removes the zero-normalization workaround and family 5, whose 65-envelope producer I couldn't confirm is feasible.
  - Drop the control counter (finding 1).
  - Replace the authorization counter with a backend check plus one "extra pass used" bool.
  - **Keep the two lane-attempt counters.** They are cheap, and one seam (`_process_queue_message`, `:5172`) covers both the watcher and the explicit drain routes. The REQUEUE refill is real: the invalid ID is popped at `:5788`, requeued and re-indexed at `:5425-5432`, and re-added at `:5677`.
  - Correct the stated reason for those counters, though. Without them the public lane is bounded at about 2 × 129 attempts per turn, not unbounded. They keep today's per-turn limit unchanged.
  - With these changes the dataclass is no longer needed.

**6. P2: The first pass is not byte-for-byte equivalent to the baseline.**
- Several changes also hit the first pass, and SQLite, which never gets an extra pass:
  - counters charged from turn start;
  - active-list retention inside the scope;
  - closing the flag on seed failure (a charge before seeding);
  - the changed meaning of the trace field.
- This muddies attribution under the strict elapsed-time rule.
- **Suggested fix:**
  - Retain the active-list entry only after a submission in this pass.
  - Keep flag behaviour as it is at registration (`:1481`) unless charge-before-seed is justified separately.
  - Leave the trace field alone.

## Test-design findings

- **7. P2, family 6 (pressure):** The amendment doesn't say how pressure gets in between receipt 1 and selection 2. Block on the reactor thread inside a forwarding wrapper on the second `_read_postgres_connection_usage` call in the same turn. The pattern already exists at `test_measured_admission_pg.py:411-421`. Also assert that receipt 1 was applied in the same `process_once`. Otherwise the test only exercises next-turn admission, which is already covered.
- **8. P2, family 4 (internal attempts):** A pending control row can't hold the priority-100 queue active. The turn-start drain (`:8384`) consumes it, and stalled rows aren't actionable (`:5026-5027`). Use an eligible pending public request instead (`:5032-5043`), and observe directly that `_drain_priority_queues` actually ran (`multiqueue_watcher.py:1580-1583`).
- **9. P3, wording:** §4 says "If C1 was rejected, restore its initial-phase exception…". It should say: remove C1's exception and restore B's reset position, spec and lesson. Add the mirror rule as well: retire C1-only keepers if C1 is rejected.
- **10. P3, suggestion:** Before writing code, take the existing B/C1 trace artifacts and measure how often the receipt was already queued when the post-pass drain started. In trace mode, count extra passes opened. Zero opened should mean the evidence is unavailable, the same rule as B's gate observations.

## Separate observations (not blockers, all pre-existing and not made worse)

- **Internal REQUEUE livelock:** With Manager REQUEUE, one malformed internal row makes the priority loop spin forever inside a single turn (`multiqueue_watcher.py:1522-1539` with `manager.py:5425-5429`). C2's internal counter would happen to bound it, but that fix disappears if C2 is removed. It deserves its own ticket.
- **Public REQUEUE starvation:** Under REQUEUE, a poison public row permanently starves valid public work. `_fetch_next_message` always fetches invalid hints first (`:5772-5777`), and the requeue re-indexes the row (`:5432`).

</details>

Author fresh-eyes on simplified C2: the fixed two/one-pass structure plus the sole pre-seed flag write proves the authorization bound without another counter. Forced pre-delivery convergence inherits that flag and cannot qualify an extra pass; the extra-pass finally closes tail permission even after denied admission. Two source-attempt counters remain justified by actual REQUEUE refill, not frontier size. Existing child-maintenance checks are reused rather than copied. The explicit tradeoff is one bounded delay before other tail maintenance, and changed failed-attempt/aggregate-fetch behavior on the first pass; these remain subject to tests and measurement, not claims of byte-identical behavior. Independent re-review is pending.

### Fresh C2 baseline, 2026-10-28

Four new B-before batches completed at frozen63dd4752 with the unchanged harness, normal12workers, actualPG300 ceiling, autostart and child-source provenance. 612 raw checks passed. These fresh samples are separate from C1 and the earlier A/B/A. The baseline collects no C2 implementation or retention evidence.

| Backend/mode | Elapsed samples (seconds) | Receipt-only turns |
|---|---|---|
| sqlite/quiet | [4.494232416, 4.169684458, 4.311773459] | [None, None, None] |
| sqlite/trace | [4.333815334, 4.116365333, 4.760051542] | [24, 24, 24] |
| postgres/quiet | [4.666170583, 5.097863459, 5.051055084] | [None, None, None] |
| postgres/trace | [5.894320167, 5.995223583, 5.821479917] | [0, 0, 0] |

Raw `dispatch-measurements/C2-B-before/`; source state `c2-baseline-run-state.json`; verified data `C2-B-before-verified.json`, all under the execution directory. B remains the candidate baseline; concurrent workers remain deferred.


## C2 closure review round2 and round3 dispositions (2026-10-28)

Round2 verdict remains BLOCKED until independent review accepts the following plan corrections. No C2 runtime/test edits are authorized by this record.

| Finding | Disposition for round3 |
|---|---|
| Exhausted internal quota permits priority inversion | Added shared public prefetch guard; existing pending helper gains explicit unknown policy. Real pending, empty and read-failure witnesses distinguish all outcomes. |
| Pass2 witnesses depend on receipt timing | Added bounded real publication barrier; inherited drain must apply actual receipt. Batch third-ID mutant remains reachable. |
| Empty drain retains stale retry deadline | Both existing empty observations clear stalled state before True. |
| Stale C1 test identifier | Removed from operative inventory; historical reviews remain verbatim. |
| Missing B implementation mapping | Added exact spec03 mapping replacement and spec05 strict-priority replacement. |
| Failed receipt / explicit fallback ambiguities | Successful submission with failed settled receipt can qualify; unhandled fallback publication cannot. |
| Classification frontier freshness | Reuse observed/indexed frontier under MA-1.8; explicitly disclose at most one extra dispatch of discovery delay, with classification-continuation veto firing test. No new refill. |
| Pending termination and second-pass pressure | Actual SIGTERM-recording path at settled boundary; tagged real second-pass usage read and existing configured80/reserve0.1 fixture, no measurement-capacity change. |

<details><summary>Independent round2 review, verbatim</summary>

**Verdict: BLOCKED on one item.** The fix is one sentence, and one more amendment is needed before the RED tests are written. I made no edits and ran no tests; everything below comes from reading HEAD `a385f3e8`. I could not check the 72/72 receipt figure in F10 because those artifacts are outside the repo.

## Answers

- **Can it be implemented confidently?** Almost.
  - The attempt-bound proof holds:
    - The only launch call is `manager.py:5566`, not 5568 as the plan says.
    - The only other override is `weft/commands/_spawn_submission.py:536`, which just delegates; add it to the inventory.
    - The flag gates are at `:5183`, `:8597` and `:8609`. The priority loop exits when a pass processes nothing (`multiqueue_watcher.py:1534`).
  - The receipt proof also holds: the active map is cleared only at `:1291`, `:1494`, `:1838` and in final cleanup.
  - The open problem is finding 1: the plan leaves one test oracle and one spec contradiction unresolved.
- **Does it hurt robustness?** Only through findings 1 and 2. I found no new wait, no overlapping launch, and no gap in custody or STOP handling.
  - Pass 2 can only start with an empty active map, so `_admission_allows_queue` (`:2948`) reads usage fresh.
  - The startup window (`:2955`), the `_scheduler_undurable` gate (`:5191`) and B's child gate (`:5196`) all run again on every pass-2 delivery.
- **Correctness:** One defect is new (finding 1). Everything else checks out.
- **Elegance and use of existing primitives:** Good. Two lane counters plus one bool is about the minimum. Small simplifications are listed below.

No reconsideration requested.

## Blocking

**1. P2: Running out of internal attempts lets public work jump ahead of pending internal work.**
- **Sequence:** Say the internal queue holds 128 malformed rows, then a valid internal request V, and an eligible public request P is waiting.
  - The internal loop (`multiqueue_watcher.py:1522-1535`) uses all 128 internal attempts and then stops.
  - The lower-priority pass that follows (`:1545-1553`) includes every active non-PEEK queue, so P is reserved and launched ahead of V in the same turn.
  - The explicit public drain would do the same.
- **Why it matters:** This contradicts [MF-6] at `05:1398-1399` ("strict-priority over ordinary work") and the plan's own rule "Pending internal work wins at next free lane". Neither the spec text nor the plan mentions this case.
- **Effect on tests:** Test family 4 deliberately sets up "129 invalid internal + an eligible public source", but never says whether P may be reserved. The implementer can't write that oracle.
- **Recommended fix:** While the internal counter is exhausted and the internal source still has pending work, the public gate also yields for the rest of the turn without marking the source inactive. This matches the existing rule that an internal admission retry blocks both lanes (`:2899-2900`).
  - Add one sentence to the 03 and 05 replacements.
  - Test oracle: no public reservation in that turn; next turn V launches before P. Removing the public yield must fail the test.
  - Compared with today, the poison-REQUEUE internal row still bounds the turn instead of looping forever. It does keep public starved, as today, which stays the separate observation.
- **Alternative:** Keep the behaviour as drafted, but state the exception in the spec deltas.

## Required before writing the RED tests

**2. P2: The tests that need pass 2 depend on timing.**
- Tests 1 and 5, the pass-2 maintenance staging and the C2 batch-bound row all need receipt 1 to be ready before BaseTask's post-delivery drain (`base.py:1494`).
- The plan says only "require it is applied within the same `process_once`". Under xdist load such a test will fail intermittently.
- **Fix:** Add a forwarding wrapper on `_launch_child_task`. After the real call returns True, it blocks with a timeout on the owner thread until the real worker result is queued. This is the same kind of allowed barrier as the pressure actor.
- For the pressure test, reuse the configured admission ceiling pattern from `test_measured_admission_pg.py:332` rather than relying on the server's limit of 300.

## F1–F10 check

| Finding | Status |
|---|---|
| F1 | Fixed. One nit: an early `return True` on a post-handle `next_pending is None` (`:5144`) would skip `_clear_stalled_control_message()`, leaving a past retry deadline that causes one extra 0-timeout turn. Simpler: return True only from the existing empty-queue path at the top of the loop (`:5106`), and False at the cap. That costs at most a missed pass exactly when 32 controls drained. Callers at `:5167`, `:8380` and in the tests ignore the result. |
| F2 | The inventory is correct. 8889, 3256 and 2129 do break: the pre-seed fence at the call site makes a fake `return True` count as a submission with an empty active map, which opens pass 2. Cases that stay valid: 1873, 4028 and 9591 call outside a turn, so the budget record is None; 2460 opens pass 2, but public admission stays blocked; the startup-window, classification-continuation and cleanup-reuse tests hold. The name `test_late_replayed_receipt_without_active_launch_defers_dispatch` doesn't exist at HEAD; drop it. |
| F3 | All five anchors match exactly: `05:1356-1358`, `03:472-474`, `03:882-891`, `07:1191-1194`, `lessons.md:311-314`. No other one-launch-per-turn rule remains in the specs. Also extend the B implementation mapping at `03:896-900`. |
| F4, F8, F9 | Fixed. The reset at `:8366` comes before `:8370`. The C1 tests are gone. `_drain_priority_queues` runs only with more than one active priority (`:1580`), which the eligible public source provides. |
| F5, F6 | Sound, apart from finding 1. `launch_started_this_turn` is emitted on every timed phase, including admission (`:878`), so the new meaning shows up in traces as documented. |
| F7 | The barrier phase and tag are well specified. It needs finding 2. |
| F10 | A valid signal. Pass 2 requires an empty map, so it always emits `queue_dispatch`. Two spans in one turn therefore mean an extra pass opened, and vice versa. |

## Non-blocking (P3)

- **Failed receipts can open pass 2.** A worker-reported launch failure also empties the map. That is safe, because `_schedule_admission_retry_for_source` blocks the lane. Say so in the 05 and 03 text, which currently says "successful submission … receipt settlement".
- **Explicit public drain submissions never open pass 2.** Nothing drains results after `_drain_public_spawn_requests` (`:8430`). Note this for attribution.
- **Pass 2 selects from the turn-start classification.** A leaf that arrives after that point loses to an older independent family one pass earlier than it would today. Either state this, or call the existing bounded `_classify_family_requests()` at the boundary.
- **Where the budget record lives.** Own it in `Manager._process_reactor_turn` (`:8341-8345`) with `try/finally`. That avoids re-indenting the C901 turn body with its many early returns.
- **Backlog-scaling test.** `test_completion_to_next_real_launch_does_not_scan_sibling_backlog` may now count one more real launch inside its counting window. It should stay within its ceilings, but confirm.

</details>


## C2 round3 approval and promotion, 2026-10-28

Contained cross-family review completed in455.55s with PASS, no P1/P2 and no reconsideration request. Root independently read the final result. Accept all four P3 precision refinements: restrict the existing classification candidate assertions to the first dispatch span so its mutation reaches the authorization oracle; assert the tagged pressure call occurs before the same owning turn returns; record barrier timeouts for assertion outside any broker handler exception boundary; describe a new early return at the existing post-handle empty observation accurately. Strict internal priority, real publication barriers and the current classified frontier are approved. The optional family-policy cross-reference is declined because the promoted MA-3 text already names MA-1.8 and states the exact bounded discovery delay; no second classification refill is added. Diagnostic stall logging and pre-existing internal poison starvation remain observations, not scope expansion.

Author fresh-eyes verified the shared public reservation paths, the existing internal has_pending error policy, the actual terminal service-worker publication ordering, and pending-signal handling only at the outer process_once boundary. These support the proposed forwarding barriers and guard mutations without fabricated receipts or a second result drain. Tail-maintenance/discovery delay remains an explicit measured tradeoff.

Promoted exactly the four before-runtime replacements from this reviewed plan to specs03 and05, at retained B runtime a385f3e8; source mappings and lesson will be promoted with runtime implementation. Exact approved seven replacements are retained in /tmp/weft-implementation-20261027/c2-approved-spec-replacements.json. No runtime edit or performance-retention claim accompanies promotion. RED tests, mutations, implementation reviews and fresh B/C2/B comparison remain required.

<details><summary>Round3 final review, verbatim</summary>

**Verdict: PASS.** No P1 or P2 issues remain.

All five prior blockers and the required pre-RED item are closed, and the round-3 corrections are implementable against HEAD `a385f3e8`. Every symbol and spec anchor the plan relies on exists as described. What's left is four P3 items: three tighten tests so they can't pass trivially, and one is wording. The plan's RED, mutation and measurement gates still have to be run after approval. I made no edits and ran no tests.

## Prior blockers

| Prior item | Status | Evidence |
|---|---|---|
| Internal work could be overtaken by public work once the internal attempt limit ran out | **Closed** | • `_internal_spawn_pending` (`manager.py:8497-8510`) returns False when no internal queue exists and on its existing `(BrokerError, OSError, RuntimeError)` catch. Adding the keyword lets only the new guard treat a failed read as "pending"; the convergence callers at `:7805`, `:7983` and `:8022` keep the default.<br>• Both public routes reach the same gate. The watcher path goes through `multiqueue_watcher.py:1545-1553`. The explicit fallback goes through `_drain_spawn_requests_from_queue` (`:8603`) and `_drain_round_robin_pass` into `Manager._process_queue_message`, so one guard at the gate covers both.<br>• The 128-malformed-row test works as written. The default `reserved_policy_on_error` is KEEP (`taskspec/model.py:833-834`), so malformed rows stay in the reserved queue and don't return to the inbox. V is therefore first on the next turn.<br>• The exactly-128-then-empty case and the injected read-failure case separate "known empty" from "unknown". Mutants that drop the guard, turn unknown into False, or deny whenever the limit is spent each fail at least one case. |
| Pass-2 tests depended on timing | **Closed; the barrier is feasible** | • The launch worker marks itself finished (`service.py:576-579`) before it publishes `result` or `error` (`:580-584`). Both go through `self._publish_worker_result`, which is looked up on the instance at call time (`:538`, `base.py:759`), so a forwarding wrapper intercepts them.<br>• The event's `request_id` is the child TID (`manager.py:1488-1492`, `service.py:354`), so it can be matched per ID.<br>• The worker never needs the owner thread, and no transaction is open during dispatch: the reservation move happens in `_fetch_next_message` before simplebroker's `_dispatch`.<br>• Pass 2's `_start_service_worker` won't hit "already active", because the handle is cleared before the result is published.<br>• The receipt is applied by the inherited drain after delivery (`base.py:1491-1495`), which has 64 result slots minus those used by the earlier drain. |
| Stale retry deadline when the control drain empties | **Closed** | `_drain_control_queue_first` (`:5104-5155`) clears stalled state at `:5106-5108`. The peek after handling (`:5144`) can return True early. Exactly 32 drained controls then reports empty, and 33 hits the cap and returns False, so the return-True-on-cap mutant fails. The only test patch (`test_manager.py:1342`) returns None, which reads as falsy and is harmless. |
| Stale C1 test name; missing B mapping | **Closed** | All anchors match: `03:472-474`, `03:882-891`, `03:896-900`, `05:1356-1358`, `05:1398-1399`, `07:1191-1194` and `lessons.md:311-314`. The migration override (`_spawn_submission.py:536-541`) only delegates to the Manager handler. |
| Pending-termination and pass-2 pressure witnesses | **Closed** | • `note_termination_signal` only appends to an in-memory queue and wakes the loop (`base.py:2153-2171`).<br>• Nothing on the spawn path checks pending termination in the middle of a turn. It is processed only at the next `process_once` entry (`base.py:1438`), as a graceful drain (`manager.py:4918-4952`). So the mutant that drops the check reserves the successor.<br>• `_admission_allows_queue` has a single caller (`:5208`), so tagging it is unambiguous.<br>• The existing fixture really uses 80 / 0.1 → 72 (`test_measured_admission_pg.py:322, 332-333`). With one unfinished startup, H=57 ≥ W=6, so without pressure the unmutated pass 2 would reserve. That makes the test able to fail. |

**Item 6, the classification clarification, fits the existing family policy.** [MA-1.8] already scopes ordering to "dynamically observed ancestry" (`03:445`) and to "eligible indexed work" (`03:498`). It already says frontier reuse "consume[s] known indexed work directly" (`03:502`), and that late-discovered work rejoins when it is classified. Baseline already has the same gap between classification and dispatch; C2 widens it by one authorization and says so. Pass 2 also selects from current scheduler state, not a cached candidate: `_fetch_next_message` and the success commit both clear `_family_candidate` (`:5799`, `:1702`).

## P3: required test precision (non-blocking)

1. **Classification test: scope the checks to the first pass.** Its `queue_dispatch` checks (`test_manager_family_maintenance.py:1435-1445`) run on every span. When the classification veto is removed, the second span fails first, at `candidate.request_id == ids[0]`. The test still fails, but at the wrong line. Run those checks only on the first span, so the mutant reaches `authorizations == [ids[0]]` (`:1458`).
2. **Pressure test: prove the tagged call happened in pass 2.** Also assert that the tagged `_admission_allows_queue(public)` call fired before the owning `process_once` returned. Otherwise, if pass 2 never opens, the tag fires on the next turn and "request 2 never reserved" passes without testing anything.
3. **Barrier timeout must not be lost.** A timeout raised inside the wrapper runs under simplebroker's `_safe_call_handler`, which may swallow it. Record the timeout and assert after the turn. The planned per-ID publication and same-turn application checks already catch this; the note just keeps the cause clear when it fails.
4. **Wording.** "Both existing empty paths" is slightly off: today only `:5106` returns on empty. The second is a new early return at the existing `:5144` peek, which costs no extra read.

## Out of scope / for your decision

- When the explicit fallback is denied by the internal-exhaustion guard, `_maybe_log_public_dispatch_stall` (`:8541-8542`) will log a "stall" for that turn. This only affects diagnostics.
- Optionally, add a cross-reference sentence in the [MA-1.8] paragraph (`03:502`) pointing to the new rule that pass 2 adds no classification refill, so readers of the family policy see the one-authorization discovery delay without going to [MA-3].
- A poison internal row that keeps being requeued still starves public work, as it does today. That stays a separate issue, as you instructed.

No reconsideration requested.

</details>


### C2 implementation entry and qualified RED evidence (2026-10-28)

The round-three plan review passed before runtime edits. The preimplementation qualification is recorded in `/tmp/weft-implementation-20261027/c2-red-qualification-handoff.md`, with each exact failing assertion and superseded setup failures identified. Parent inspection confirmed the intended missing second PG submission, 129th internal/public fetch, forbidden public reservation on unknown internal state, failed-seed successor reservation, missing boundary controls, missing second-pass maintenance reachability, and missing boolean empty-control evidence. SQLite batching, existing checkpoint/start custody, real held-worker PING/STOP, SIGTERM and classification cases are keepers, not newly demonstrated RED. No runtime mutation or GREEN result is claimed by that handoff.

The candidate now implements the approved two-PG/one-SQLite pass structure and outer-turn lane budgets. GREEN, mutation sensitivity, implementation review and surrounding-baseline measurements remain open. The pressure witness additionally installs a late internal request after second-pass public selection and requires the extra grant to be closed at tail convergence. This proves the phase boundary described above; a missing reclosure may authorize a second attempt there, so it must not be described as necessarily proving a third launch.


### C2 implementation review and dispositions (2026-10-28)

Fresh-eyes runtime review found no actionable defect (`c2-runtime-fresh-eyes-review.md`); that reviewer authored the pressure test but not runtime. Cross-family Claude review completed666.25s, blocker F1 for a missing priority-retention witness, with no runtime defect found. F1 accepted: batch has two older public rows and two internal rows; exact first-turn and later order must survive, and removing source-hint retention must fail. F2 accepted: move allowance closure and submission proof into existing `_launch_child_task`, removing duplicate flag assignment and retiring the new C901 exception. Ruff and registry reconciliation pass without a new suppression. Standalone behavior and new mutation anchors remain execution gates. F3 accepted: reclosure scopes an unused extra grant; after an actual second attempt the pre-seed write already closed it. F4 accepted: document consolidation of fake PING/old STOP tests into real held-worker control witnesses. F5 accepted: map the actual allowance, readiness, delivery and launch owners above. Full verdict follows; accepted changes still require independent closure review.

Pre-correction candidate verification: SQLite focused40pass/2PG-onlyskip; full SQLite5981pass/65skip in303.56s; mypy480files and Ruff/format/index passed. Eleven Docker parity skips are unverified, not passes. PostgreSQL startup never reached pytest because local Docker requests hung; only owned blocked clients were stopped and the environment was restored. Possible owned container `weft-pg-test-85976-9f1b0fa4` must be inspected after Docker recovers. No C2 performance result exists. These results predate the F1/F2 and pressure-test corrections.

### Pressure witness refinement: independent injection before admission

Replace the earlier instruction to install external pressure inside the tagged usage-read wrapper. Tag the actual second public `_admission_allows_queue` call only while the same owning `process_once` is active and first actual launch receipt has been applied. At that boundary, before forwarding the real admission owner, use the existing bounded child-ready-file barrier and pressure actor acknowledgement to install real external connections. Choose the additional count from a real baseline observation to target U=74; do not substitute usage, capacity, admission, or receipt outcomes. Enqueue the late internal request at this same boundary, after priority selection has already passed, using its real source queue and third held-child release path.

Independently record real usage before and after installation. After the owning turn returns, require the tag fired, the actual child-ready barrier completed, pre-pressure U was below the startup fast-path threshold floor(72*fraction), the actor acknowledged its requested cumulative count, and post-pressure 72<=U<80. These premises must pass before the exact historical reservation oracle. The candidate's own forwarding usage observer then independently requires a fresh read at the tagged decision with the same pressure range. Keep that read assertion after custody, so a cached-read mutant fails because it actually reserved forbidden work under proven real pressure, not because an observer was skipped.

The earlier inside-read placement secured ordering, but coupled pressure creation to the very read a stale-cache mutant removes. Moving injection to the tagged admission boundary preserves ordering while making the stale-cache experiment a real pressure violation. This is an explicit refinement of the approved placement, not a changed production contract.

Keep natural first-turn tail convergence and actual internal-drain observation, without forcing any deadline. Under public-only pressure the internal lane retains real headroom; removing extra-pass final reclosure must let tail convergence reserve the late internal request and fail the same historical-custody oracle. This proves the conservative ordinary-grant scope boundary, not necessarily a third authorization: when the extra pass made no attempt, the forbidden tail attempt can be attempt two. After pressure release, require real progress in first-public, late-internal, second-public order.

No added wait primitives or changed timeouts, workers, server capacity, fixture admission ceiling, reserved policy, or runtime behavior. Existing B tag-missing RED remains relevant to opening the extra pass, but this revised pressure placement requires candidate PG GREEN and both mutants after the freeze. Until executed, mutation mechanisms are proposals, not results.


# C2 bounded serial dispatch review (delta against `359d8d8f`)

**Verdict: blocker: F1.** I found no defect in the runtime code. F1 is a test gap: one approved C2 safety property has no test that would catch its removal. The smallest fix is one extra request in one existing test.

I read the program theory, the decision hierarchy, engineering principle 11, the review runbook and the dispatch plan through Verification. I also read the C2 review and disposition records and the live code paths. I ran nothing and changed nothing. The RED evidence is your verified record, not something I re-ran. The GREEN, mutation, full-suite and B/C2/B performance gates are still open. Nothing here means the work is complete.

## Findings

| ID | Severity | Location | Finding | Suggested fix |
|---|---|---|---|---|
| F1 | P2 | `test_manager_family_maintenance.py:956`, `:1791`; `manager.py:5201-5208` | **Keeping source hints after a submission has no failing test.** This is what keeps internal priority in pass 2. Without it, pass 1's second gate visit drops a still-pending internal queue from `_active_queues`. The receipt's `_resume_startup_dispatch` puts only the public inbox back (`manager.py:1881`). Pass 2's `_update_active_queues` does not search again: the precheck flag was already used, and discovery is not due ms later (`multiqueue_watcher.py:1338-1351`). Pass 2 would then reserve public ahead of pending internal work, which [MF-6] forbids. No current test exposes this. The batch test has only one internal request. The `initial_pings` case has two, but checks only `reserved.count(ids[0]) == 1`. This mutation is also missing from the declared mutation list. | Add a second internal request to the batch test, written after the two public ones. On PG, expect `reserved == [I1, I2]` in the first turn with both public rows still in source. On SQLite, expect `[I1]`, then I2, P0, P1. Add "remove the retention branch" to the mutation list. By my reading the broken version would reserve P0 in pass 2 every time; confirm by running it. The existing third-pass check still works (P0 becomes the forbidden third). |
| F2 | P3 | `manager.py:5614-5616`, `:5635-5640`; registry `RUFF-SUP-381` | **The new C901 suppression is avoidable, and its stated reason doesn't fit the two new branches.** Neither branch uses the source/reserved row. Both belong to the existing launch owner. `_launch_child_task` already sets the same flag at `:1500` and is the only launch caller. Moving `:1500` to the top of `_launch_child_task`, before the seed, closes the allowance before seeding for every failure and removes a duplicate write. Setting `submitted_in_pass` just before `return True` (`:1518`) records the submission. The order after the final control check (`:5605`) is unchanged. My reading puts `_handle_work_message` at 10 before this delta (the limit) and `_launch_child_task` at about 10 after the move. That is not verified; run ruff. One side effect: a seed, class or draining failure outside a turn would also set the flag, which the next turn clears (`:8427`). Check the standalone handler tests. This is moving code to its existing owner, not a cosmetic extraction. | Owner's choice. Either move the two writes, retire RUFF-SUP-381 and restore the counts, or keep the suppression and rewrite its reason to the real one (both writes must follow the final pre-seed control check). |
| F3 | P3 | `03-Manager_Architecture.md:929-930`; plan `:193` | **Wrong reason in the spec.** "…so tail convergence cannot authorize a third request" is inaccurate. If pass 2 attempted a launch, the pre-seed write and `:1500` already closed the flag. The `finally` only matters when pass 2 opened but made no attempt. In that case it stops tail convergence from making a *second* attempt, which is exactly the accepted risk. The code comment at `manager.py:8494-8495` is correct. | Wording only. For example: "so tail convergence cannot attempt another authorization after an opened extra pass, even when that pass made no attempt." This is reviewed, promoted text, so the owner decides. No reversal requested. |
| F4 | P3 | plan `:223`, `:237`, `:518`; Deviation Log `:554-557` | **The plan's test list no longer matches the delta.** The plan says to keep `test_manager_reactor_answers_ping_while_child_launch_is_active`; the delta deletes it. It says to modify `…stop_command_does_not_launch_new_children_after_stop`; the delta deletes and replaces it, dropping the `task_spawned`/`control_stop` log checks. The replacement held-worker test is stronger: a blocking turn would trip `hold_timeouts`, and exact reservation history is checked. But the Deviation Log is empty. | Record the consolidation in the plan's test list or Deviation Log. No code change. |
| F5 | nit | `03:932-940` mapping | **Code mapping in the spec is out of date.** It says `_process_queue_message` owns the attempt limits and the internal-exhaustion rule. The actual owner is the new `_spawn_dispatch_allowance_open`. `_extra_dispatch_pass_ready` and `_process_manager_delivery_pass` are not mapped. | Name them in the mapping so the backlink check stays clean. |

## Answers

**Is it correct?** Yes. I traced each point you listed in live code:

- **Budget lifetime:** the budget is created and cleared in `_process_reactor_turn` (`:8398-8406`). Stopping turns and calls outside a turn see `None` and keep B's behaviour.
- **Pre-seed allowance charge:** it comes after the final control check. The new failure it covers is seed failure (also class resolution and the draining return). Checkpoint and start failures were already covered at `:1500`. Forced convergence before the scope therefore closes pass 1, as the plan intends.
- **Submitted/applied settlement:** the active map is cleared only by receipt handling, launch exceptions (which return False), the stale-launch retry (turn start only) and final cleanup. "Submitted in this pass and map now empty" therefore means the receipt was applied. The fallback public drain has no result drain after it, so it can never qualify.
- **Strict priority at the aggregate limit:** the check runs only once the internal budget is spent, and treats a failed read as pending. Convergence callers keep their default. A denial is not marked inactive, and every loop exits on `processed == 0`.
- **First-pass receipts:** a receipt applied at turn start closes the flag, `submitted_in_pass` stays False, and pass 1 makes no attempt. Same as B.
- **Control drain cap and empty proof:** both empty observations clear stalled state. Exactly 32 handled then empty returns True; reaching the cap returns False. Callers that ignore the result are unaffected.
- **Maintenance gate:** the scope stays on across both passes, so B's sentinel/deadline check runs before every reservation in pass 2.
- **Pending termination and classification:** both are checked before and after the boundary control drain. Neither can be cleared mid-turn.
- **Tail closure:** it is unconditional on leaving pass 2 (see F3 for the wording). Counting all paths: at most two attempts per turn on PG and one on SQLite.

**Is it elegant and defensible to an expert systems programmer?** Yes, with F2 noted. It adds one three-field record, two constants, a bool return and one keyword policy. It reuses the existing allowance flag, the active map as receipt proof, the 128-message lane limits, the shared gate in `_process_queue_message` and the B maintenance check. It adds no waits, workers, counters or result drains. The fixed two-pass loop with a guarded preamble reads more clearly than straight-line code with a duplicated `try/finally`. The double readiness check around the control drain is justified: the first check avoids a control read on turns where pass 2 can't open.

**Tests.**
- **Barriers are real.** Each test waits on actual worker publication, and the real inherited result drain applies the receipt. Timeouts are recorded and asserted outside the handler. Markers placed after the handler (`readiness`, `boundary`, `tagged_gate`, `pressure_installed`) defeat swallowed assertions.
- **Cleanup order is right.** Releases are touched, then worker threads joined, then processes joined, then patches restored.
- **The late internal arrival in the pressure test does fire the missing-reclosure mutation.** At 72 ≤ U < 80, public is denied but internal is allowed (`internal_limit = C`, `admission.py:63-71`), and `tail_usage` proves the tail drain actually ran.
- **Other declared mutations reach a forbidden reservation on PG,** except retention (F1). The SIGTERM and boundary cases pass on SQLite without testing anything (it has one pass), which is acceptable.

**Is the C901 suppression necessary?** Not strictly; see F2. Refusing small single-caller helpers is right. But moving the two writes into the existing launch owner is not a helper. It is the smaller, more cohesive change.

## Observations (not actionable this pass)

- A control row that permanently fails to delete makes the boundary drain return False every turn. That quietly turns off batching until the row is resolved. It is a conservative degradation, not a safety issue.
- `_child_launch_started_this_turn` now means "ordinary allowance closed": it covers receipts, attempts and the extra-pass closure. The name and trace field are out of scope, and the plan already documents that it is not a count.
- A poison public row under REQUEUE still loops across turns. It is now bounded at 128 fetches per turn instead of roughly 258, so it got better, not worse. The `public_dispatch_stalled` log entry on internal-exhaustion denial was already noted in the record.
- Nit: the `_dispatch_cleanup_scope` part of the submission condition (`:5637`) is redundant, because every pass starts by resetting `submitted_in_pass` and the tail runs after the loop. It is harmless as a statement of intent.
- Nit: if a worker hangs, the `assert` in `retire_dispatch_workers` can hide the test's original exception during teardown.

I made no edits. This session had only read and search tools, so no plan file was written.


### C2 review-correction verification (2026-10-28)

Corrected candidate:355 targeted SQLite tests passed,5 PostgreSQL-only cases skipped (`c2-review-corrections-sqlite.log`), including standalone launch/handler and recovery owners after moving the attempt write. Nine SQLite mutations each failed at the intended source assertion and their exact restored candidate passed: SQLite pass2, internal-cap omission, public-cap omission, public-cap reset at fallback, pending-internal priority omission, unknown-internal fail-open, delayed preseed charge, retained post-handle stalled state, and unconditional public denial despite known-empty internal quota. Parent inspected actual extra reservations,129vs128 fetches, stale timestamp and missing-public-progress assertions, not only runner exit status. Original bytes and whole git diff were restored after every mutation; evidence is `/tmp/weft-implementation-20261027/c2-sqlite-mutations/`. PostgreSQL-only mutations, including the new source-hint retention witness, remain unexecuted. The corrected pressure fixture and both pressure mutations likewise require PostgreSQL GREEN. No C2 measurements have been run.


### C2 corrected review closure and local verification (2026-10-29)

Independent fresh-eyes review of final Manager SHA256 `2706cc17517d533705961e282df72b61f830af98e16d749f1e3d9acf163a6757` found no actionable defect (`c2-corrected-fresh-eyes.md`). Cross-family closure completed381.70s with **no blocker**; full verdict follows. F1–F5 and independent pressure injection are closed in source, not by unperformed PostgreSQL execution. N2 accepted: corrected the operative pressure instruction and listed the retention mutant above. N3 accepted: removed duplicated no-attempt wording in MA-3 and its plan mirror. N1 accepted as an explicit mutation-evidence condition: periodic inactive-queue rediscovery can rescue the deliberately dropped internal hint. A retention-mutant pass must stop the runner; inspect actual rediscovery timing before classifying it. Do not weaken the oracle or blindly repeat until failure. No discovery-policy override was added to production or tests.

Final corrected full SQLite:5981passed,65skipped in319.85s (`c2-corrected-full-sqlite.log`), including11 unverified Docker parity skips caused by the unresponsive local Docker engine. Full mypy480files, Ruff, formatting633files and suppression-index reconciliation pass. All pinned source hashes match after the nine qualified SQLite mutations and restored-node GREEN confirmations. The complete remaining runtime gate is eleven PostgreSQL core mutations plus two pressure mutations, PostgreSQL GREEN/full suite, and the unchanged B/C2/B measurements; the closure review's three highlighted mutants are not an exhaustive remaining inventory. No candidate timing or performance-retention claim is made.

Local Docker remained unresponsive to a bounded5s inventory request after tests finished. OrbStack reports running; restart permission remains pending because other containers cannot be inventoried. PostgreSQL startup did not reach pytest. Recover Docker, inspect the exact possible owned container from `c2-local-docker-block.json`, then complete the remaining gates. Concurrent launch workers remain deferred. CI-D2 implementation remains deferred until measurements preserve the common test foundation. No commit, push or merge of C2 has occurred.

**Verdict: no blocker.** F1–F5 and the pressure-injection correction are closed in source and tests. I found three P3 items and some observations; none of them reopens a finding. I only read files: no edits, no tests. This session has no Write or ExitPlanMode tool, so the review is here and not in the plan file.

The execution gates are still open and nothing here closes them:
- PostgreSQL GREEN for the batch and pressure tests.
- The three PostgreSQL-only mutants: source-hint retention, cached-read, and missing reclosure.
- The full corrected SQLite run that is in progress, and the full PostgreSQL run.
- The B/C2/B performance comparison.

The SQLite counts are your recorded evidence, not something I re-ran.

## Closure of F1–F5

| ID | Status | Evidence |
|---|---|---|
| F1 | Closed | The batch test (`test_manager_family_maintenance.py:969-1017`) writes P0 and P1 first, then I1 and I2. On PostgreSQL, first-turn `reserved == [I1, I2]`; on SQLite it is `[I1]`. Both public rows must still be in the source, and later progress must be in the exact full order. Two internal rows are what make this test the retention witness. After I1 is reserved, the internal queue still has a pending row, so the inherited watcher (`multiqueue_watcher.py:1441`) does not drop it. The second gate visit (`manager.py:5204-5211`) is then the only place that decides whether it stays. A third-pass mutant reserves P0 and fails both checks. See N1 for one timing condition on the mutant. |
| F2 | Closed | The allowance write is now at the top of `_launch_child_task` (`manager.py:1405-1406`). The old write after registration is gone. The submission proof is recorded just before `return True` (`:1519-1520`). Nothing was added to the handler, and its path from the final control check (`:5608`) into the launch call is the same as in B. The only launch caller is still `:5617`. Moving the flag earlier has no effect inside the same call: nothing between entry and `_start_service_worker` reads it. The only other readers are the drain loops (`:8686`, `:8698`), the allowance gate and the trace. A flag left set by a standalone call is reset by the next normal turn (`:8421`). |
| F3 | Closed | `03:929-931` now gives the correct reason: closing the allowance limits only an extra grant that was opened but not used. It matches plan `:193` and the code comment at `manager.py:8488-8489`. See N3 for one duplicated clause. |
| F4 | Closed | Plan `:223`, `:237` and `:524` record the move of the PING and STOP tests into `test_manager_services_control_while_real_launch_receipt_is_held` (`:1957`). The old tests are gone. No claim of keeping the old log-order checks remains. |
| F5 | Closed | The mapping at `03:937-952` names the actual owners: the budget, the pass loop, the allowance gate, readiness, the delivery pass, internal-pending, control drain, handler and launch. |

## The pressure correction

It is correct and does what you intended. I traced it against live code:

- **Tag placement.** The tag requires `evidence.applied == [ids[0]]`. That is only true after the post-delivery drain in pass 1. Pass 1's own fallback is skipped because the flag is closed. So the first tagged public admission is pass 2's. The tag also fires again on pass 2's fallback drain. That is harmless: the install guard prevents a second injection, and both reads land in `tagged_usage`.
- **Late internal row.** It is written while the pass-2 public gate is being evaluated. By then `_update_active_queues` and priority selection have already happened, and both the round robin and the fallback only cover public. So a GREEN run cannot legitimately reserve it in pass 2. That matches the accepted tail delay.
- **Cached-read mutant.** `_observe_admission_usage` (`manager.py:2252`) reads fresh every time, so the mutant has to add a cache. With pressure now installed outside that read, a cached mutant still sees the tag fire. Every premise passes (ready child, actor ACK, 72 ≤ U < 80), and the test then fails at the custody check (`test_measured_admission_pg.py:971`) with P1 reserved. This fixes the old problem, where the mutant would have failed only because an observer was skipped.
- **Reclosure mutant.**
  - This turn is the first convergence call, so the tail is not throttled (`manager.py:8002-8005`). It sees internal work pending, sets the precheck flag and calls the wrapped internal drain.
  - Without reclosure the flag is open and the internal budget is live, so admission allows internal at U below 80. I is reserved and the same custody check fails.
  - In a correct run, the drain's flag check stops before the round robin. The `tail_usage` check then makes a skipped tail fail loudly instead of passing quietly.
- **Premise order.** The tag, readiness, baseline, ACK and installed-range checks all come before the custody check. `wait_for_file` raises on timeout and the marker is set after it, so a swallowed exception still fails.

## Findings

| ID | Sev | Location | Finding | Suggested disposition / smallest fix |
|---|---|---|---|---|
| N1 | P3 | `multiqueue_watcher.py:1338-1351`; `_constants.py:629`; batch test `:992-996` | **The retention mutant is not killed on every run.** This corrects my earlier review, which said it would reserve P0 "every time". Only broad rediscovery can bring the dropped internal queue back, and that runs on a 1.0 s interval. Say the last probe was at pass 1's start (T0). The internal queue is dropped after the probe check that follows I1's handler. If T0 + 1 s falls after that check but before or at pass 2's update, rediscovery restores the queue and the mutant passes. That needs I1's publication wait just under 1 s plus receipt handling. It is unlikely, but possible on a loaded host. A correct run is unaffected, because rediscovery only adds queues. | Accept as an evidence condition. When the PostgreSQL mutant is run, treat a pass as unclassified until pass-1 timing has ruled out rediscovery. Do not record it as a real survivor yet. Optional deterministic pin: set `manager._inactive_probe_interval` high before `process_once()`. This file already pins discovery at `:478` and `:574`. Owner's choice: it adds work and depends on precheck hints for later progress. |
| N2 | P3 | plan `:263`, `:246` | **The plan contradicts itself.** `:263` still tells you to install pressure inside the tagged usage-read wrapper. `:2596` says to replace that instruction, so someone reading top-down meets the old one first. Item 1 at `:246` does not list the retention mutant either. It is recorded only in the dispositions (`:2590`, `:2660`). | Wording only. Rewrite `:263` in place, or add a pointer to the refinement. Add "remove source-hint retention must fail" to item 1. |
| N3 | nit | `03:929-931` | The extra-pass sentence says "even if it made no attempt" and then "even when that pass made no attempt". | Delete the second clause. Promoted text, so the owner decides. |

## Answers

**Is it correct?** Yes. Each pass still makes at most one attempt, and the allowance is closed before any failure point inside `_launch_child_task`. A pass only counts as submitted after a successful worker submission inside the ordinary dispatch scope. Tail and forced convergence cannot open an extra pass: the scope guard stops them from setting `submitted_in_pass`, and every pass clears it at the start. Totals stay at 2 attempts per turn on PostgreSQL and 1 on SQLite.

**Is it elegant, and does it use the real owners?** Yes. The F2 move removes a duplicate write and the C901 suppression; the allowance write now lives with its existing launch owner. The pressure test uses the real admission gate, the real usage read, a real held actor, real child readiness and real reservation history. It fakes no usage, admission or receipt. The plan does change where the test injects pressure, and it says so. No production contract changes.

**Is it falsifiable?** Yes, for the retention, third-pass, cached-read and reclosure mutants, subject to N1 and to running them on PostgreSQL. A mutant that skips admission entirely would fail at the `tagged_gate` check, not at custody. That is still a kill, and it is covered by the existing tag-missing RED from B.

## Observations (not actionable)

- `runtime_identity_for_task(...)` is evaluated as a call argument (`manager.py:5627`), so a lineage-validation exception happens before the new allowance write. B behaves the same way, and it is a validation rejection, not a launch attempt, so it is not made worse. "Closes before seeding" is still accurate.
- The pressure test's GREEN margin is small. It targets U = 74 with a window of [72, 80). If more than two connections drop before the fresh read, P1 would legitimately be admitted. `tagged_usage` would show that, so it is something to watch on the PostgreSQL run, not a design issue.
- The `_dispatch_cleanup_scope` part of the submission guard (`:1519`) is still redundant, because every pass resets `submitted_in_pass`. As before, it does no harm and states the intent.


### PostgreSQL recovery, survivor diagnosis and measurement provenance (2026-10-29)

After owner-restored Docker, the exact possible startup container was absent; unrelated containers were left untouched. Candidate PG correctness passed48cases at default12 (`c2-recovered-green-pg.log`). The PG third-pass mutant failed at exact extra submission and restored GREEN. The checkpoint submitted-onFalse mutant then **survived**,1passed (`c2-pg-mutations/submitted_false_checkpoint/record.json`); the runner stopped, restored exact source/diff and environment, and no kill was claimed. Diagnosis: PostgreSQL capacity0 means automatic server capacity, not disabled admission. `_handle_child_launch_failure` schedules a real public-lane retry, so a wrongly opened extra pass still cannot reserve the successor. Earlier scratch descriptions of disabled PG admission were incorrect.

Keep all actual custody assertions and real retry policy. Extend the existing failure test with a forwarding observer of completed `queue_dispatch` phases and require exactlyone in its first turn. MF-6/MA-3 forbids another complete pass without actual successful submission; this tests unnecessary owner work directly, without claiming a custody failure masked by another guard. Checkpoint/start mutant expectations now target two-versus-one completed phases. This refinement requires new GREEN, empirical mutations and focused independent review. No runtime code changed.

Measurement-script audit added explicit matching/pinning of `pyproject.toml` and `uv.lock`, plus available/unavailable batching-opportunity reporting from actual PG second spans/authorizations. Current dependency inputs match frozen B; benchmark/harness bytes remain unchanged. Historical B-before retained exact SimpleBroker source/Python/arguments but not simplebroker-pg/psycopg fingerprints or working-tree lock bytes. The comparison may therefore be reported only with that dependency limitation; matching current lock blobs cannot retroactively prove installed package identity. This limitation is accepted for running the predeclared comparison, not for an unqualified causal or final retention claim. If a numerical speed benefit would justify keeping C2, reassess whether this provenance uncertainty requires the already-bounded additional complete comparison; never silently manufacture a fingerprint or select favorable repetitions. Audit evidence: `c2-dependency-comparability-audit.md`. Quiet criteria are unchanged.


### PostgreSQL mutation qualification (2026-10-29)

All13 PostgreSQL mutations now fail their intended assertions, with exact source restoration and same-node candidate GREEN: thirdpass (`c2-pg-mutations/pg_three_passes`); checkpoint/start false-submission completed-pass violations; missing control drain, cap falselyempty, pendingSIGTERM, classification continuation, sentinel, deadline, cleanup-scope, source-hint retention, cachedpublicusage and unused-extra-grant tailreclosure (`c2-pg-mutations-v2/`). Parent inspected exact assertions and actual extra IDs/two-versus-one spans. The original checkpoint survivor remains preserved as the reason for refining the test; no safety guard was removed. The revised three-case failure test also passed on both backends. Source-hint removal actually selected a public ID before the second internal ID; periodic rediscovery did not mask this execution. Both pressure mutants reached forbidden historical reservation only after independent readiness/ACK/real headroom premises. These are empirical results, not predicted mechanisms. Full PostgreSQL and final focused test review remain before timing.


### C2 execution/review gates before timing (2026-10-29)

Full PostgreSQL passed5946tests,29skipped in351.97s (`c2-full-pg.log`), normal12 workers/native300. The earlier Docker parity skips are covered by this restored-Docker run; live-provider and backend-specific exclusions remain explicit skips. Exact candidate Manager source remains SHA2706cc17517d533705961e282df72b61f830af98e16d749f1e3d9acf163a6757. The final three-case failure-test refinement passed both backends; all13PG and9SQLite mutation mechanisms have qualified actual assertion failures with restored GREEN.

Focused cross-family test review completed72.80s, **no blocker**, and confirms that completed-pass counting is the negative counterpart of the existing maintenance witness, not a custody-defect claim. O1 optional seed-path submittedFalse mutation is not added: seed attempt charging has its own empirical fault mutation, and checkpoint/start exercise the actual synchronous exception path where submission evidence must remain false. No exhaustive every-return mutation claim. O2 accepted: SQLite exercises compatibility, not sensitivity of the PG-only extra-pass rule. O3 observer lifetime left unchanged because it forwards real work and is asserted only at the first-turn boundary. Full verdict follows.

The read-only measurement audit additionally pins `bin/pytest-pg` alongside pyproject/lock inputs; current wrapper matches frozen B and its recorded baseline Git blob. Opt-in diagnostic difference is only definitions/disabled code during collection; no diagnostic sink is activated, and benchmark elapsed starts after integrated preflight/warmup. It is not a claim of identical module-definition import cost. Historical PG package fingerprint limitation remains as recorded above. No performance result has been selected or measured for C2 yet.

**Verdict: no blocker.**

I only read code and docs. I didn't run anything. The mutation results below are the ones you supplied, not reruns of mine.

## Does the completed-phase count prove the contract?

Yes, without claiming a custody defect.

- **The spec:** `docs/specifications/03-Manager_Architecture.md:896-901` says only "successful submission and receipt settlement within the preceding pass" may open the extra PostgreSQL pass. So the extra pass is forbidden work in its own right. It doesn't need to produce a wrong reservation to break the contract.
- **What gates the second pass:** in `weft/core/manager.py:8474-8485`, a second pass needs `_extra_dispatch_pass_ready` (`:8553-8562`), which first requires `budget.submitted_in_pass`. That flag is reset before each pass (`:8483`) and set in only one place, `:1519-1520`. That line is reached only after both `_checkpoint_admission_launch` and `_start_service_worker` return normally inside the dispatch scope.
- **Where each fault exits:**
  - Seed fault: returns at `:1435`, before the launch is registered.
  - Checkpoint and start faults: go through `except` at `:1513-1518` (`_clear_active_child_launch`, then `_handle_child_launch_failure`, then `return False`).

  None of them reaches `:1520`.
- **Why the old test let the mutant survive:** after a failure, `_handle_child_launch_failure` calls `_schedule_admission_retry_for_source` (`:1799`). With the retry gate closed, a wrongly opened second pass still can't reserve `ids[1]`. The existing no-reservation assertion was correct, but it couldn't see the extra pass. The new assertion catches exactly that gap, and the in-test comment says so accurately.
- **Custody and retry guards:** all existing assertions are unchanged. Nothing touches the reserved/submitted checks, the `peek_one` check, the retry gate, admission or the clock.

## Could correct current code legitimately run a second `queue_dispatch`?

No, for any of the three faults.

- **Another submission in pass 0:** `_launch_child_task` sets `_child_launch_started_this_turn = True` at `:1406`, before seeding. `_spawn_dispatch_allowance_open` (`:5204`) and the public fallback (`:8575`) then both close for the rest of the pass. So nothing else in pass 0 can set `submitted_in_pass`. The existing `ids[1] not in evidence.reserved` check also confirms this.
- **Pass 0 skipped:** `queue_dispatch` would be skipped only if a launch were still active (`:8566`). All three fault paths clear or never register one, so exactly one completed phase is the only correct count.
- **Log level:** the phase is counted whether or not trace logging is on. `real_timed` runs the operation either way (`:849-854`).

## Is the test correct, falsifiable and well-placed?

Yes.

- **Correct:** recording after `real_timed` returns means only completed passes are counted. A pass that raised would already have failed the test in `process_once`.
- **Falsifiable:** your records show both the checkpoint and start mutants failing exactly at `assert dispatch_passes == ["queue_dispatch"]` with two phases instead of one, and the restored source passing.
- **Right boundary:** the observer is the same forwarding wrapper the sibling positive test uses (`test_extra_dispatch_pass_rechecks_child_maintenance`, `:1950-1966`, which asserts two passes). This refinement is its negative counterpart. It wraps the real owner's existing phase seam with no new production hook, fake receipt or readiness stub, which is the right place for it.

## Optional observations (not actionable for closure)

1. **O1 – seed path not mutation-tested.** Both submission-flag mutants target the exception path at `:1513-1518`. A matching mutant on the seed return at `:1435` would hit the same assertion by construction, but the evidence you supplied doesn't include one. Whether to add it is your call.
2. **O2 – SQLite runs don't test this assertion.** `MANAGER_SQLITE_DISPATCH_MAX_PASSES = 1` (`weft/_constants.py:777`), so the assertion can't fail on SQLite. The 3 SQLite passes show the test is harmless there, not that the check works. This matches the accepted fixed 2-pass PostgreSQL / 1-pass SQLite policy, and the comment already scopes the reasoning to PostgreSQL.
3. **O3 – cosmetic.** The observer stays installed through `drive_until`. It forwards everything and nothing checks it there, so it's harmless. Storing the repeated `"queue_dispatch"` string works; the sibling's `len(...)` style would be equivalent. No change needed.

No standing invariants are affected, and nothing here asks to reconsider the concurrent-launch deferral or the time/load/worker policy. This review doesn't claim GREEN or full-suite results; those depend on the PostgreSQL run that's still in progress and your restoration checks.


### C2 measured decision: reject batching (2026-10-29)

The complete B-before/C2/B-after comparison finished: 12 batches, 96 scenarios, 2038 verification checks. Evidence is `/tmp/weft-implementation-20261027/C2-B-C2-B-analysis.json` and `C2-B-C2-B-verification-checks.json`, with raw datasets and source manifests alongside them. PG quiet elapsed seconds were B-before [4.666170583, 5.097863459, 5.051055084], C2 [4.852761583, 4.619805625, 4.748111625], B-after [4.924441791, 5.054709208, 4.852210416]. Only one of three candidate repetitions beat the fastest surrounding baseline (4.666170583); the required all-three criterion failed. No primary quiet metric had a reproducible regression.

The strongest counterargument is the consistent PG PING improvement: candidate [0.145752708, 0.161187625, 0.153644292] seconds, each below every surrounding baseline sample. This does not replace the predeclared elapsed-time retention criterion. Batching was exercised: measured throughput traces had [16, 14, 14] extra spans and [8, 10, 10] extra authorizations; maximum simultaneous creators remained one. Receipt-only PG eligibility remained zero. The dependency-provenance qualification above still applies. This is not proof of equivalence or that batching cannot help.

Decision: do not use the optional additional comparison to seek a favorable result. Reject C2 and restore B runtime/spec behavior, retaining independently useful real-broker test replacements and the startup-pressure fixture repair. Preserve the reviewed candidate in local Git before rollback so its implementation can be recovered. B duplicate-sweep removal remains retained for eliminating verified redundant work, without a speed claim. Concurrent launch workers remain deferred. Rollback tests, independent reviews, CI diagnostic repair and final CI gates remain outstanding.
