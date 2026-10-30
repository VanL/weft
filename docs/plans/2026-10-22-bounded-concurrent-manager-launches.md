# Measured Manager Dispatch Batching

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.1], [MA-1.2], [MA-1.8], [MA-3]; docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-6]; docs/specifications/07-System_Invariants.md [MANAGER.11], [MANAGER.18], [IMPL.8], [IMPL.10]
Superseded by: none

Class: 5. Implementation with spec revision; hardening applies to reactor control order, launch custody, and cleanup. The October 27 revision replaces the concurrent-worker-first sequence with measured serial dispatch improvements. This document authorizes no deployment. Plan authoring does not implement or promote the proposed behavior.

## Current execution decision (2026-10-29)

B duplicate-sweep removal is retained. C1 and C2 are rejected after their complete measured comparisons; their proposed behavior below is historical experiment design, not the final runtime. Concurrent launch workers remain deferred. C2 candidate is recoverable at commit `94606277`; final runtime/constants/specifications restore `359d8d8f`. Useful real-broker test replacements remain. Final verification and exact-head CI passed at `540707a3`; the plan is closed (see Closure at the end of the execution record). The temporary measurement harness was retired on 2026-10-29 and is recoverable at commit `4a98f26b`.

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
| C2 (rejected after measurement) | Retained B plus at most two serial delivery passes on PostgreSQL, one on SQLite | Are receipts ready soon enough to share maintenance within an outer turn? Is the extra loop worthwhile? |
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

The new system test path became runnable in Slice 1; it was retired on 2026-10-29 (recoverable at `4a98f26b`), so commands naming `tests/system/test_manager_dispatch_costs.py` are historical. Final implementation gates follow `docs/specifications/08-Testing_Strategy.md` [TS-4] and the actual platform-specific recipes in `.github/workflows/test.yml`, including normal-load PostgreSQL/SQLite, slow and relevant extension suites; retain the existing separate extension/integration scheduling rather than combining roots into a different load; targeted tests alone do not close the runtime plan. Record RED assertions, restored GREEN, mutation sensitivity, raw timing comparisons, and review dispositions. At final reconciliation, verify reciprocal code/spec ownership and plan backlinks rather than inventing an unavailable traceability tool.

## Rollout, Rollback, and Deferred Concurrency

No deployment is part of this work. Runtime rollout follows independently verified slices into isolated contexts, then separately authorized Manager turnover. Queue names, checkpoint formats, admission policy, startup exposure, single-worker transport and family ordering are unchanged by B/C. Rollback reverts the selected code/spec slice and uses normal Manager turnover; preserve reserved work and checkpoints, do not replay unknown execution or kill detached tasks to tidy the rollback. There is no durable migration or intended one-way door.

If D passes later, the former design's real obligations must be rederived and reviewed: exact group/request identity; unresolved requests retaining their slot; per-request stale-worker proof; selection-order lineage before another selection; successor ambiguity without local ownership exceptions; STOP during seeding; one shared destructive cleanup deadline; non-destructive drain waiting for actual producer closure before final result drain; and supported concurrent multiprocessing behavior. Preserve the historical review below as evidence of those hazards, not ready-made permission to add them. Do not promote its former [MA-1.8]/[MANAGER.18] custody exception during serial work.

Residual limits: neither ordering nor batching frees connections held by waiting parents; multiple Managers do not provide globally atomic admission; external pressure may require the existing timed recheck; custody ambiguity may need operator resolution. These are accepted existing limits, not reasons to weaken the gates.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|

The four before-runtime behavior replacements were promoted on 2026-10-28 after round3 PASS against a385f3e8. The C2 candidate runtime and with-runtime mappings were implemented, measured and then rejected; runtime and specifications were restored to B (`359d8d8f`). B is retained and C1 has been removed. Recorded deviations: the fake PING and old STOP tests were consolidated into the real held-worker witness `test_manager_services_control_while_real_launch_receipt_is_held[PING|STOP]` (see Test inventory); the opt-in measurement harness was retired after the decision (Closure).

## October 27 Revision and Review Record

Owner direction: measure bounded dispatch batching and duplicate-sweep removal before adding concurrent launch workers. Replaced the former concurrency-first operative design and deltas; preserved the earlier review record below. Author family: OpenAI. Author fresh-eyes review, failed Tier-1 attempts, and completed Tier-2 review are recorded here. The fallback is not cross-family approval.

Fresh-eyes finding A1: “batch first” could silently mean multiple active workers. Corrected with A/B/C1/C2/D arms, one lane throughout B/C, ready-only post-pass receipts, and an explicit no-wait/no-benefit outcome. A2: broad turn time is not removable overhead. Corrected with nested-span warning, independent operation counts, unchanged safety work and comparisons. A3: repeated BaseTask passes could reset drain budgets. Corrected with shared outer-turn work/result budgets and a falsifiable fairness row. A4: the old custody exception would prematurely permit concurrency. Removed it from the operative delta and explicitly prohibited its promotion during B/C. Residual risk: C2 may offer too little natural receipt readiness to justify its control-flow complexity; the plan permits rejecting it.

Plan authoring and independent Tier-2 review: complete, with accepted findings corrected and focused re-review PASS. Implementation had not begun at this point; baseline measurements and conditional spec promotion were execution gates.

### Current reviewer availability and fallback record

2026-10-27: author family OpenAI. Tier-1 Anthropic Claude Code 2.1.293 passed the 120-second liveness probe (`PROBE-OK`) and a write-attempt containment probe (no write tool, probe file absent). Review invocation: `claude -p <embedded-plan-brief> --permission-mode plan --restricted --strict-mcp-config --tools Read,Grep,Glob --allowedTools Read,Grep,Glob --no-session-persistence --effort high`, closed stdin, Python subprocess timeout 540 seconds. Both the admission and repair plan calls reached 540 seconds with empty stdout/stderr and no verdict; neither counts as review approval. Exact briefs, hashes and result records: `/tmp/weft-plan-review-20261027/admission-round1.*` and `repairs-round1.*`.

Second Tier-1 family attempt: Grok 1.0.62, `grok --cwd /Users/van/Developer/weft --sandbox read-only --always-approve --disable-web-search --output-format json --prompt-file /tmp/weft-plan-review-20261027/grok-live.prompt`, closed stdin, 120-second bound. It exited 1 before model startup because its sandbox could not resolve the `/var/run/docker.sock` deny path (a symlink outside the deny list) and it refused to start without protections. No unenforced retry was made. This availability failure applies to both plan reviews.

Tier-2 review was then selected: **same-family fallback (degraded)** via a separate native OpenAI reviewer for each plan, after the logged Tier-1 failures. This is not cross-family approval. CLI timeout diagnosis continued separately; it did not replace review or authorize changes to tool security settings.

Timeout diagnosis: a subsequent bounded Claude call with identical containment and high effort, plus stream-json/verbose/partial-message/debug telemetry, completed a real specification Read in 8.806 seconds (exit 0; model reported as claude-opus-5-5). This rules out a persistent startup/authentication/Read-tool failure at diagnosis time, but does not identify the earlier full-review timeout cause. Those failures remain unclassified timeouts, not proven model stalls or version regressions. Invocation maintenance adopted for later rounds: retain the security posture and bounded deadline, stream progress and require a final successful result with the requested verdict; progress alone is not approval. No skill or security-setting change was made. Diagnostic artifacts: `/tmp/weft-plan-review-20261027/claude-tool-diagnostic.*`.

### Tier-2 review, round 1 and dispositions

Reviewer: native OpenAI agent `/root/scheduler_evidence`, separate from the plan author; **same-family fallback (degraded)**. Unit: the October 27 operative serial-first revision against source baseline `e220748f`, excluding the superseded October 22 design. Verdict: **BLOCKED as written on S1/S2**.

| Finding | Reviewer evidence (summarized) | Disposition | Operative correction and proof |
|---|---|---|---|
| S1 [P2] | No aggregate watcher work budget exists to share: `_drain_priority_queues` drains the highest non-PEEK priority until no progress, and `max_messages` belongs only to `_drain_spawn_requests_from_queue`. A real isolated Manager probe with 129 malformed internal requests and one pending control row reserved all 129 in one `_drain_queue()` call despite `MANAGER_INTERNAL_SPAWN_DRAIN_MAX_MESSAGES == 128`. | Accept within C2 | Add Manager-owned attempted spawn-delivery counters before fetch/reservation across inherited and explicit paths, with existing lane ceilings and outer-turn reset. Count rejected/malformed attempts; preserve fresh-turn continuation. Revised design, proposed MA-3 delta, Slice 3 and acceptance matrix require the 129-row priority-topology regression. No generic watcher rewrite. |
| S2 [P2] | `_classify_family_requests` deliberately retains `_family_classification_pending` after a full bounded page and `_admission_retry_timeouts` then returns zero; a real 65-row probe showed `pending_continuation=True`, `eligible_candidate_exists=True`, timeouts `[~1.0, 0.0]`. Treating that zero as a pre-delivery veto forces whole-backlog discovery before known eligible work. | Accept within B/C | Distinguish a bounded page already serviced at turn start from newly due/unserviced safety maintenance. Preserve first dispatch of known eligible work while classification continuation remains; remaining page work may end extra C2 passes. Revised design/delta and 65-row acceptance control forbid the whole-backlog veto. |
| Result/control accounting caution | Manager calls outside BaseTask also drain. | Accept | Aggregate Manager calls as well as BaseTask pre/post-pass calls. Skip a result-drain call at zero remaining budget because `max_results=0` still drains one; do not reset through helpers. |

Focused re-review, 2026-10-27, same reviewer and degraded posture: **PASS** ("Both accepted findings are resolved in the operative plan"). The reviewer verified pre-fetch counters across watcher/explicit drains, outer-turn reset and continuation, first eligible dispatch after bounded classification, the corresponding spec delta and regression rows, the zero-result-budget skip and inclusion of Manager calls outside BaseTask. No new contradiction or blocker; no standing constraint or refused alternative was proposed for reversal.

Plan-authoring verification, 2026-10-27: metadata/spec-hygiene tests passed (6 tests, 0.98 s); `bin/check-doc-paths`, `bin/coalesce-check` and `git diff --check` passed. Coalescing evidence: 13 SHA claims (6 foreign), 208 retrieval cues (206 retired-plan cues), all local cues resolve. Plan paths/symbols were also inspected directly because the doc-path checker excludes plans. Only plan/index text and related-plan backlinks changed.

## Historical October 22 Review Record (superseded sequence)

The following is the preserved review record of the earlier concurrent-worker-first draft. Its PASS statements apply to that version only. Instructions, spec deltas, and implementation sequence above govern the revised plan; historical suggestions below cannot bypass the new D gate. Verbatim review text was condensed on 2026-10-29 (see the Record Condensation Note below).

Author fresh-eyes review, 2026-10-22: accepted S1 (P1), the duplicated receipt-before-next-dispatch clause conflicted with concurrency; added exact replacements in [MA-1.8] and [MANAGER.18], explicitly preserving successor ambiguity. Accepted S2 (P2), sentinel draining alone does not settle/reap; initially proposed exact-TID cleanup; superseded after F8/F10 by event-ready slice yield and normal next-turn cleanup, avoiding a new API. Accepted S5 (P1), freeing dead-worker slots could cycle ambiguous internal launches past the exposure bound; unresolved requests now retain slots and the internal allowance until positive resolution. Accepted S4 (P3), identifier check corrected `_drain_spawn_requests_from_queue` and named the actual family fixture rather than an assumed burst fixture. Accepted S3 (P3), group completion inspection was underspecified; named the existing snapshot and completion-before-publication ordering. Residual risk: extra crash-window exposure up to the existing six-startup bound; benchmark benefit is unproven. Cross-family plan review round 1: BLOCKED; round 2: FAIL on R2-1/R2-2, both accepted and corrected; round 3 focused verification PASS; A1 check-order clarification accepted and applied.

Reviewer inventory, 2026-10-22: Claude CLI liveness returned `PROBE-OK`; plan-mode write-attempt probe did not create the requested repository file (it wrote only an external Claude plan). Review posture: `--permission-mode plan --allowedTools Read,Grep,Glob`, as prescribed by `skills/call-agent/SKILL.md`. Repository state checked before review. Native research assistance is not counted as independent review.

### Cross-family round 1: findings and dispositions

Reviewer: Claude (cross-family), 2026-10-22, against `e220748f`. Verdict: **BLOCKED as written**; F1 alone made the draft unsafe (its new STOP fence could deliver the same work twice). Premise check confirmed: the per-turn flag gates, error routing to the first active request, stale detection via global worker activity, family registration at commit, and cited commits. Reviewer observations (not actionable): up to N launches could be left unresolved instead of one, within the disclosed bound; an existing double-seed path exists under a non-default REQUEUE checkpoint-failure policy (default KEEP); SQLite control order still preserved the May lesson's reason.

| ID | Finding (summarized) | Disposition | Operative change / evidence |
|---|---|---|---|
| F1 [P1] | `_seed_child_inbox` writes before the checkpoint and is not idempotent; the draft's post-seed STOP fence forced REQUEUE and a same-TID relaunch reseeded `T{tid}.inbox`. | Accept | Removed post-seed fence/return; authorization is the existing pre-seed check, with an exactly-once seed/work regression. |
| F2 [P2] | Drain finish, superseded shutdown and the no-children yield consult only `_child_processes`, so shutdown could finish with launches in flight and kill freshly launched user tasks. | Accept | Named all three; require in-flight drain participation. Round 2 corrected the assumed timeout: non-destructive drain waits for producer closure and receipts without a new timer; destructive STOP retains its existing deadline. |
| F3 [P2] | `_has_actionable_leadership_work` returns True while launches are active, so no leadership fence forms. | Accept | Active launches veto `_has_actionable_leadership_work`; positive lower-primary proof enters the existing revalidating leadership drain despite active launches. |
| F4 [P2] | A group is marked inactive before its result publishes, so per-group stale detection could drop a good receipt. | Modify | Inactive group does not prove missing outcome. Preserve exact unresolved request and accept late matching receipt across grace; test delayed publication. A global no-pending-results condition is insufficient alone because publication can occur after that read. |
| F5 [P2] | Six launch groups are unnecessary; one worker call per TID-keyed request suffices. | Reject implementation switch | Groups are bounded registrations, not persistent threads. Keep existing ServiceTask lifecycle and identity transport; document raw-worker alternative rather than implement a second path. |
| F6 [P2] | Benefit unmeasured; predecessor never filled its startup window (peak 3 unfinished startups). | Accept | Slice 1 became a go/no-go before code; measure phase contribution, choose smallest justified N in 2..6, measure sweep contribution without claiming it dominates. |
| F7 [P2] | [MANAGER.18] repeats [MA-1.8]; delta changed only 03. | Accept | Mirror lineage insertion and custody exception into MANAGER.18; generic non-PG fallback is one; implementation mapping and resume comment named. |
| F8 [P3] | In-pass receipt/sentinel/maintenance drains risk re-entrancy. | Modify | No nested lifecycle drains. Ready facts end the slice for an immediate next turn; retain per-authorization controls and pure due-deadline checks. |
| F9 [P3] | Existing `launched()`/`retire()`, checkpoint and recovery already cover the scheduler needs. | Accept reuse; qualify evidence | Use launched/retire and update semantics/docstrings only; removed speculative schema stop and runtime rollback probe. Startup W=6 alone is not proof of six concurrent OS starts. |
| F10 [P3] | Leave `_cleanup_children` alone; the repeated sweep is cheap. | Modify | Cost is not known from code size alone. Round 2 resolved the conflict with F8: skip the duplicate sweep inside explicit dispatch scope, retaining standalone/shutdown cleanup; measure its benefit; no subset-cleanup API. |
| F11 [P3] | Wrong names (`_drain_spawn_queue`), unnamed turn flag, unlisted cross-thread reads, unchecked concurrent `launch_task_process`/`set_executable`. | Accept | Corrected drain name, named turn flag and cross-thread reads; added concurrent multiprocessing audit to Slice 1. No unsupported GIL/free-threaded safety assertion. |
| F12 [P3] | Crash-point tests need a stated method. | Accept | Specify staged-successor helpers (`_reserve_and_checkpoint`) and child test driver around production phases, no production-only crash hooks. |

### Cross-family round 2: findings and dispositions

Reviewer: Claude, focused review, 2026-10-22. Verdict: **FAIL**; F1, F3, F4, F6, F7, F9, F11 and F12 verified at `e220748f` (F3 conditional on F2). No standing invariant reversal requested. Out-of-scope observations: the seed-then-checkpoint crash window predates concurrency; `_drain_active_child_launches_for_cleanup` drops tracking when no worker is active, with the durable checkpoint keeping custody.

| ID | Finding (summarized) | Disposition | Operative correction |
|---|---|---|---|
| R2-1 [P1] | F2's fix assumed a non-destructive drain time limit that does not exist (`_continue_shutdown_drain` and `_manager_drain_timeouts` time only destructive drains; `_finish_graceful_shutdown` resets `_drain_stops_children`), so "reason-aware late cleanup" needed new state. | Accept | Removed invented turnover expiry and reason-aware late cleanup. Non-destructive completion waits for actual worker producer closure plus result drain, not inactive group handles; unresolved dead-group custody survives completion. Destructive timeout predicates include active launches with zero committed children. |
| R2-2 [P2] | Proposed [MA-3] "no recursive lifecycle drain inside queue delivery" contradicted the retained handler `_cleanup_children()` sweep. | Accept | Require dispatch-scoped sweep suppression with try/finally, preserve standalone/shutdown behavior, and prove both call paths. |

### Cross-family round 3: verification

Reviewer: Claude, final focused verification, 2026-10-22, against `e220748f`. Verdict: **PASS** on R2-1 and R2-2, with one suggested wording change (A1); no invariant reversal, no RECONSIDERATION REQUESTED. A1: `_has_worker_activity()` checks pending results before live threads, so a result published between the two checks could be applied after the `*_drained` event and the new child stopped during cleanup.

A1 disposition: accept. The shutdown section explicitly checks actual live worker threads before pending results, forbids the oppositely ordered `_has_worker_activity()` shortcut at that boundary, and requires an inter-check publication regression. This clarifies the already-reviewed producer-closure ordering; no policy change.

Plan-authoring verification, 2026-10-22: `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py` passed (4 tests); `bin/check-doc-paths` passed; `git diff --check` passed. Existing operative paths and symbols checked against the source, including `_reserve_and_checkpoint` in the recovery tests.

Owner review requirement (2026-10-27): before completion, perform both a fresh-eyes implementation review and a separate other-model/cross-family review on the final combined delta. Both must explicitly answer: Is it correct? Is it elegant enough for an expert systems programmer? Does it use the underlying data structures properly or overcomplicate them? Verify findings against actual code, firing tests, theory and measurement evidence; retain dispositions and focused re-review of accepted fixes. Earlier slice reviews do not replace these final passes.

## Execution and Review Record (2026-10-28 to 2026-10-29)

### Record condensation note (2026-10-29)

This record was condensed on 2026-10-29 under `docs/agent-context/runbooks/writing-plans.md` §8 by `2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md` Task 4. Reviewer metadata, findings and dispositions, verification command summaries, decisions and measurement tables are kept; verbatim reviewer transcripts, the one-time fault-probe source and repeated raw pastes were removed. The uncondensed text is recoverable with `git show 540707a3:docs/plans/2026-10-22-bounded-concurrent-manager-launches.md`. Paths under `/tmp/weft-implementation-20261027/` and `/Users/van/.codex/worktrees/` are disposable local evidence, not required inputs.

Reviewer metadata for every entry below unless stated otherwise: author family OpenAI; Tier-1 cross-family reviewer Anthropic Claude (Opus 5.5) through the contained read-only invocation recorded above (`--permission-mode plan`, Read/Grep/Glob only, no session persistence, bounded subprocess timeout); reviewers ran no tests and made no edits, so execution results are the author's evidence. "Fresh-eyes" entries are same-family author-independent reviews and are not cross-family approval.

### Pre-B clarification and prerequisite repair (2026-10-28)

Tier-1 Claude review (375.72 s) identified B-F1: a consumed terminal publication retry can remain expired while broker recovery starts fresh proof grace. Real owner probes confirmed stale `[0.0]` versus reset `[15.0]`, then `[14.0]`, with reserved custody retained and real second-request dispatch. This existing [MA-1.6a] no-spin violation would become a dispatch stall under B's veto. The repair is S2 in the runtime repair plan and entered every arm's common baseline before measurements.

B-F2/F3/F4 accepted: sentinel observation is a hint; gate every delivery including the first; scope the no-recursion statement to child-cleanup commits, preserving normal worker-result receipts and other lifecycle owners. B-F5 accepted: reuse existing Event/deadline readiness without another notify call. B-F6 accepted: measure yield turns and reject a meaningful latency regression unless the owner explicitly accepts the observed tradeoff. Narrow child-only B policy preserves non-child phase order; extra-pass maintenance belonged to conditional C2.

Round-2 Tier-1 review (388.96 s): **PASS** for the revised plan and S2 prerequisite (not for unimplemented B). Nonblocking R2-F1–F4 accepted: exact S2 normative-delta traceability, first-delivery wording, predeclared latency distributions/repeatability rule, and advancement/retirement for all child deadline families.

B implementation preparation: reuse `startup_pg_manager` in `tests/core/test_manager_family_maintenance.py` (it owns physical PG headroom) rather than copying it; the old-child readiness case stages a real process and exact reservation at the cleanup owner, the second request uses actual launch, retry expiry uses a Manager-local advancing offset clock, and readiness nonconsumption is observed at the gate return.

### Measurement harness review and correction (DC-1 to DC-22, 2026-10-28)

| Round | Reviewer / duration | Verdict | Findings and dispositions |
|---|---|---|---|
| Attempt 1 | Claude, timed out at 540.58 s (exit 143) | none | No approval inferred; tighter brief and 900 s bound used next. |
| Attempt 2 (frozen draft SHA-256 `6e77eb72…`) | Claude, 553.45 s | One conditional blocker (DC-1); did **not** read required theory/governance context, so not a completed gate | See table below. |
| Correction review (draft `e6049f83…`) | Claude, 563.90 s, required context read | Blocker DC-14 | DC-14–DC-20 accepted (below). |
| Final correction review | Claude, 312.54 s, required context read | **No blocker**; correctness, elegance and primitives pass | DC-14–DC-20, type fixes and body-only API correction closed; DC-21 accepted (neutral C1 structural retention uses trace counts only and requires zero censored receipt turns in every compared sample); DC-22 declined as cosmetic. |

| Finding | Disposition before measurements |
|---|---|
| DC-1 | Child-import provenance was declared but not implemented. Accept the stronger proof, not inferred PYTHONPATH: a small real spawned-function preflight inside the pytest invocation, with its verified artifact required at completion. |
| DC-2 | No symmetric benefit rule. Accept before any data: all three candidate quiet elapsed values below both surrounding baseline minima. Structural-neutral retention remains allowed for B/C1; C2 requires the elapsed benefit. |
| DC-3 | Multiplicity (≈32 comparisons). Accept named primaries: quiet elapsed, PING call-to-return and STOP handling latency; other series secondary; matched-ordinal criterion removed. No significance claim. |
| DC-4 | `blocked_startup` re-derived the predicate without the empty-`_startup_feedback` guard. Accept exact guard; do not call a mutating owner merely to observe it. |
| DC-5 | Zero B gate observations would look like zero yields. Require observed B gate calls; absent coverage means unavailable maintenance evidence and an open gate. |
| DC-6 / DC-7 | Accept derived handler-sweep counts and parent start-spacing as secondary evidence from existing raw records. |
| DC-8 | Terminal-proof oracle reads only `ctrl_out`. Kept pending smoke; after source/spec check the oracle was retained (explicit STOP terminates/reaps tracked children under [MA-3]); all smoke custody artifacts validated twelve accepted requests. |
| DC-9 | Retain the declared 300-second experiment bound; a timeout is a failed run to diagnose, not a sample. |
| DC-10 / DC-11 | Accept STOP-only closure attribution and preserving primary failures while recording diagnostic/cleanup errors; sole cleanup/summary errors still fail. |
| DC-12 / DC-13 | Accept accurate handoff/receipt naming and all four per-arm invocation recipes. |
| Craft | Share control/receipt observation; remove unused cleanup timing and redundant source copy; no hook-table framework. |
| DC-14 (P1) | The source probe expected a payload argument for the empty mapping (`prepare_call_arguments` passes none), so every run failed before warmup. Accept no-argument probe. |
| DC-15 | Windows backslash manifest keys. Accept portable keys. |
| DC-16 | `git diff HEAD` omits untracked files. Accept explicit untracked-source accounting (`git ls-files --others --exclude-standard`). |
| DC-17 | Reused artifact parents could pair an old completion with a failed rerun. Accept a fresh parent per invocation (atomic `mkdir`). |
| DC-18 | No receipt-only-turn field; backend of benefit rule unstated. Accept complete receipt-only-turn counts with partial-turn censoring, PostgreSQL benefit and both-backend nonregression criteria (now in the operative rule). |
| DC-19 / DC-20 | Accept removal of vacuous closure flags/assertions (retain actual preflight ownership and manifest hash verification) and `raise primary from None`. |

Correction (2026-10-28): the attempt-2/correction review's claim that BLE001 was not selected and that the suppression index ignored untagged reasoned directives is false. See [harness policy correction proof](#harness-policy-correction-proof-2026-10-28) for the raw-audit failure and repaired ledger.

Integrated smoke and author verification: isolated probes reproduced the empty-payload error, Windows key shape, untracked-file gap, reused-parent ambiguity and misleading exception chaining; corrected controls passed. The first real smoke failed the custody-body assertion because low-level SimpleBroker `peek_one` defaults to `(body, timestamp)`; both reads now request `with_timestamps=False` (a harness API error, not a scheduling failure). Final smoke source SHA-256 `aa038ff9529d279d060927be1e370b8ae66a30980ed9ac643b726d58df1d73d1`: SQLite quiet 24.81 s, trace 24.38 s; PostgreSQL quiet 45.01 s, trace 48.84 s; each with real pipeline preflight, one warmup and three throughput/STOP repetitions; all 32 scenario directories and four completion markers verified; twelve custody rows in each of sixteen STOP artifacts. Unmodified six/eight-parent acceptance passed (2 SQLite 6.65 s, 2 PostgreSQL 10.48 s). Smoke runs validate the instrument; they are not the A baseline. Final gates: plan/spec hygiene passed with the two opt-in experiments skipped (skips are not measurements); Ruff/format, suppression registry, full mypy (479 files), doc paths and whitespace passed (the registry claim was later found wrong; see the correction proof).

### First A batches retained; B promotion (2026-10-28)

Common repaired A is `8123b129`, frozen in a detached baseline worktree with its own virtual environment. All four normal 12-worker batches passed: SQLite quiet 24.76 s, trace 24.56 s; PostgreSQL quiet 45.34 s, trace 48.31 s. Each had an empty source diff, the same committed harness, verified actual-child source provenance, warmup plus three measured throughput/STOP pairs and complete raw artifacts. No B source or tests were applied before these results.

B's proposed [MA-3] paragraph was promoted text-first against `8123b129`, before implementation. B retains one authorization and receipt-consumes-turn. Comprehension checks: a free lane is not authority; no waiting for receipts; missing evidence never permits reseeding; newly ready child maintenance yields to its normal owner.

### B implementation, falsification and A/B/A evidence (2026-10-28)

B implements only the promoted ordinary-delivery scope and pre-reservation child-maintenance observation (Manager SHA-256 `e5d98ca0a5428e5e23d1899704dc82619d679a4d699b313b556e90d36cf70fbc`; new-test SHA-256 `16c51addc4031c63c54eafa037fc9b97e70612b4b89eaf4acfa0966a7aab0787`). Tests live with the existing real family-maintenance fixture. RED: four intended failures and three passes on each backend (earlier fixture errors corrected before acceptance). GREEN: fifteen focused/keeper cases per backend, then the complete 51-case family gate on each backend, including unchanged six/eight-parent acceptance. Ten isolated mutants produced twelve intended assertion failures; seven positive controls passed; all fourteen owned PIDs were absent afterward.

All twelve A/B/A batches (96 warmup/measured scenarios) completed with matched provenance, normal twelve workers, unchanged capacity/autostart. A-before and A-after are the same clean `8123b129` checkout; B is that commit plus the frozen B diff. Primary quiet samples, seconds, in measurement order (rule fixed before A-before):

| Backend / metric | A-before | B | A-after |
|---|---|---|---|
| sqlite / elapsed_seconds | [4.033703209, 4.430341833, 3.975394167] | [4.153165042, 4.370222125, 4.131000125] | [4.328474375, 4.635464375, 4.419605875] |
| sqlite / ping_call_to_return_seconds | [0.014500083, 0.015966208, 0.012072375] | [0.013300041, 0.012900167, 0.011812708] | [0.015394291, 0.015246834, 0.121546916] |
| sqlite / stop_handler_return_seconds | [0.002635084, 0.002430875, 0.002530875] | [0.002668041, 0.002757834, 0.009014958] | [0.003194625, 0.003694834, 0.002961958] |
| postgres / elapsed_seconds | [4.687358625, 4.742724583, 4.739174792] | [4.739131542, 4.950271834, 4.742491833] | [4.935938334, 5.023572291, 5.119909375] |
| postgres / ping_call_to_return_seconds | [0.167205250, 0.174405792, 0.172763250] | [0.182297625, 0.175903292, 0.167479416] | [0.175720292, 0.178162459, 0.170451458] |
| postgres / stop_handler_return_seconds | [0.043082208, 0.049754041, 0.049808375] | [0.051847541, 0.053241042, 0.048893333] | [0.053029625, 0.047690584, 0.052007041] |

No primary quiet series met the reproducible-regression rule; neither backend met the elapsed-benefit rule. PostgreSQL elapsed and SQLite STOP have disjoint surrounding baseline ranges, so attribution is uncertain; the 9.015 ms B SQLite STOP and 121.547 ms returning-A PING samples stay in the record. These data are neither a speedup nor proof of causal neutrality. Every trace repetition removed exactly 24 handler sweeps. B gate-call counts: SQLite 26/24/24, PostgreSQL 34/34/32 (positive coverage closes the instrumentation gate). Maintenance yields: SQLite 2/0/0 across 1/0/0 turns; PostgreSQL 10/10/8 across 5/5/4 turns, all sentinel-driven. Independent reconstruction: all 72 measured B PostgreSQL receipts were committed inside their authorization turn; the 72 SQLite receipts came from prior turns.

**Decision:** B qualifies under the redundant-sweep-removal clause with no demonstrated primary regression; this is not a performance-benefit claim. No concurrent workers are justified by these observations.

Full SQLite verification of that tree: 5,973 passed, 38 skipped, 3 failed (239.80 s). All three were measurement-harness hygiene: two direct `simplebroker.__file__` uses violated the architecture guard, and three reasoned BLE001 catches were absent from the global suppression inventory (two policy failures). No runtime case failed; repaired in the harness policy correction below.

### B completed-work independent review (2026-10-28)

Tier-1 attempt 1 timed out at 540.26 s (exit 143); the retry completed in 455.76 s, exit 0, required context confirmed: **no blocker**; correct, elegant, primitives used properly.

| ID | Finding (summarized) | Disposition |
|---|---|---|
| F1 [P3] | The sentinel hint can be set with nothing to service (event set after drain, or a reaped child's queued event), costing one ordinary turn. | Accept as a measurement limitation; stale-hint fraction is unavailable from current artifacts. No new polling, wake or adapter change. |
| F2 [nit] | Spec whitespace and `_Implementation mapping_:` style drift. | Accept. |
| F3 [nit] | Classification test duplicated fixture teardown. | Accept; rely on the fixture owner, keep `release.touch`. |
| F4 [nit] | Optional trim of the completed-result wait in the reuse test. | Decline; it proves both delivery routes finish and supports clean teardown. |

Corrections verified: classification continuation plus plan/spec hygiene 7 passed on SQLite (1.98 s); the classification case passed on PostgreSQL (6.59 s). B is retained as the serial baseline.

### B PostgreSQL runtime verification (2026-10-28)

Normal 12-worker PostgreSQL run: 5,925 passed, 29 skipped (364.63 s) with `bin/pytest-pg --all -- tests -k 'not test_weft_uses_only_supported_simplebroker_surfaces and not test_approved_suppressions_match_the_standalone_registry and not test_real_suppression_index_check_is_clean'`; only the three known harness-policy failures were excluded, as explicit post-correction gates. A first invocation without argparse's `--` was rejected before any test ran and is not a pass.

### Harness policy correction proof (2026-10-28)

| Round | Reviewer / duration | Verdict |
|---|---|---|
| Catch-retention review | Claude, 357.26 s; required runbook/invariant context incomplete | Blockers F1, F2 (mechanical); all three catches NET POSITIVE |
| Complete-context closure | Claude, 297.00 s; full context confirmed | **No blocker**; F1–F7 pass; R1–R3 accepted |

| ID | Finding (summarized) | Disposition |
|---|---|---|
| F1 [P1] | New registry rows were placed after the table's blank line, so the parser never read them. | Accept: rows inside the table. |
| F2 [P1] | Pinned policy constants (`EXPECTED_GROUP_IDS`, group/directive counts) must change atomically. | Accept: 198 groups, 329 directives. |
| F3 [P2] | The only proof was a disposable `/tmp` probe. | Owner-offered minimum alternative: the complete one-time fault controls and mutation definitions were preserved in this plan (now recoverable at commit `4a98f26b`) instead of adding routine tests for an opt-in harness. |
| F4 [P3] | The control catch's protection holds only on the normal join path. | Accept: qualified wording. |
| F5 [P3] | Probe did not assert the fallback `summary_error` content. | Accept: assertion added. |
| F6 [P3] | The earlier review's "BLE001 unselected / untagged suppressions ignored" premise was false (raw `--ignore-noqa` BLE001 = 136). | Accept: correction pointer added without rewriting the earlier record. |
| F7 [nit] | The edit changes harness identity. | Accept: old-source measurements stay tied to their hash; all future B/C1/B arms use identical new bytes; no pooling. |
| R1 [P3] | Plan anchors dangle after retirement. | Accept: ledger records the immutable proof-containing commit. |
| R2 / R3 [nit] | Correction pointer placement; probe scratch directory. | Accept. |

Per-catch decisions (both rounds, independently): `_control` `except BaseException` keep, NET POSITIVE (an Exception-only catch silently loses `SystemExit` and pytest outcome exceptions; executor/Future moves the same catch into the stdlib and changes the measured window); `_persist_sample` summary and write-loop `except Exception` keep, NET POSITIVE (finite lists miss real classes; outer catch skips later artifacts). `inspect.getfile(simplebroker)` replaces `simplebroker.__file__` without changing the dependency guard.

One-time author-run controls: original measured harness SHA-256 `2a033aa341206bb82c1fae148e3806d85beae475c645af2e2073d722d5d33640`, corrected `e581597e4ad92df7c0bcb6232a976219a7cd342ba3013b53998654bf75484130`; module path, source-tree hash and actual manifest equal; simplebroker SHA-256 `5280db42103df9c96febba14e234c9080dcaa3a068e6c0ae2581181bd512760c`. Eight positive cases passed: ProbeFailure and FatalProbe exact control-error transfer; summary and first-write faults with and without a primary failure preserving later artifacts and error precedence; fatal diagnostic exceptions propagating from summary and first write. Three mutants (RUFF-SUP-379 `BaseException`→`Exception`; each RUFF-SUP-380 catch `Exception`→`OSError`) failed with exit 1 at the intended assertions ("thread failure identity not transferred"; "diagnostic failure escaped before later artifacts" ×2).

Closure: full default SQLite 5,976 passed, 38 skipped (177.15 s) with no exclusions; focused architecture/policy/index/plan/spec set 144 passed (4.83 s); Ruff, format, full mypy (479 files), doc paths and whitespace passed; full PostgreSQL `bin/pytest-pg --all` with no exclusions 5,928 passed, 29 skipped (355.69 s). This closed the harness correction gate. Later disposition: the harness file and `RUFF-SUP-379`/`RUFF-SUP-380` were retired on 2026-10-29 by `2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md` (see Closure below).

### C1 clarification and reset relocation (2026-10-28)

| Round | Reviewer / duration | Verdict | Disposition summary |
|---|---|---|---|
| First C1 clarification | Claude, 396.56 s, context complete | Blocker F4 (P2) | F4 accepted: promoted spec must retain one new launch per turn. F1's fewer-reads preference accepted, but a smaller candidate selected: move the existing allowance reset to just after the initial bounded result drain, so only initial-phase receipts are exempt. F2's impossibility claim rejected: stale recovery can clear an active entry before a queued result reaches the pre-pass drain. F3/F5/F6 accepted as test-design corrections; F7 accepted as a limit (retry deadlines exist only with admission enabled); F8/F9 accepted. |
| Round 2 (reset relocation) | Claude | Blocker F1 (P2): the [MA-1.8] wording dropped the startup-settlement liveness guarantee | Accepted with closure refinements below; runtime relocation itself had no blocker. |
| Closure review | Claude | **No blocker** | Closure F1–F7 accepted (table below). |

| Review finding | Disposition and operative action |
|---|---|
| Closure F1 | Accept. The [MA-1.8] second-sentence replacement qualifies receipts only in the initial bounded phase, keeps startup settlement and later immediate continuation, and preserves startup room, empty-source idling and blocked-backlog suppression. |
| Closure F2 | Modify. Public late STOP on PostgreSQL with all competing gates proven open in real and mutant runs; late internal priority in the same-process replay keeper; no sensitivity claim until the receipt-assignment mutant fails the exact reservation assertion. |
| Closure F3 | Accept. Early STOP uses an internal successor, reservation history and a positive same-turn STOP assertion; its mutant removes the initial control drain (ordering keeper, not C1's RED). |
| Closure F4 / F5 | Accept. Coupling comments at the receipt assignment and moved reset; receipt-or-settlement wake comment, retaining the existing unconditional wake. |
| Closure F6 | Accept. Narrow the operative text, spec delta, slice and acceptance row; revise the May lesson with a historical marker; update the [MANAGER.18] mapping with code. |
| Closure F7 | Accept. Exact-turn tests use direct `process_once`. |
| Round-2 F1–F8 | Accept with closure refinements (settlement and receipt liveness, observed reservations, open gates, fully queued and phase-pinned receipt, two eligible successors, "new child launch" for B/C1 and attempted authorization only for C2). |
| First clarification F1/F2 | Modify/reject respectively, as above. |

Strongest accepted limitation: the reset relies on initial result callbacks not authorizing; both edit points are marked and the exactly-one launch and late-receipt boundaries are tested rather than adding a second state variable.

C1 execution: text-first promotion against `63dd4752fdff1e3a1da42187d881d0f1942565ce` before runtime edits. RED qualified on both backends (two intended progression failures, four keeper passes; SQLite 3.68 s, PostgreSQL 7.75 s) after correcting fixture phase expectations and fixture reaping ownership. A real subprocess probe confirmed psutil reaped a process while its multiprocessing handle retained `exitcode=None`/`is_alive=True`; the fixture now closes worker threads, joins children through their Process owners, then runs Manager cleanup (the pre-existing production cleanup diagnostic remains a separate observation). GREEN: owner module SQLite 31 passed (21.34 s), PostgreSQL 31 passed (25.70 s). The early-STOP mutation initially failed only the eventual STOP assertion; the final observer raises at the first forbidden reservation (SQLite 2.77 s, PostgreSQL 6.63 s).

C1 implementation review: Claude, 464.06 s. Runtime no blocker; F1 reported as a test-proof blocker assuming a single-site undurable mutation. Dispositions: F1 modify (the executed mutation already removed both readiness and reservation terms and both backends failed `failed checkpoint opened ordinary reservation`; two-site contract clarified); F2 accept (already executed); F3 accept ([MANAGER.18] mapping cites closure under [MF-6] and continuation under [MA-1.8]); F4 accept (actual receipt enqueue plus observed initial phase); F5 accept (drop malformed reserved-policy assertion); F6/F7 accept (docstring, rewrap). Eight mutation executions produced 12 intended failures (old reset 2/backend, removed late-receipt flag 2/backend, deleted initial control drain 1/backend, both ordinary custody gates removed 1/backend). Closure review: Claude, 89.68 s, **no blocker**, F1–F7 closed; the reviewer corrected its single-site assumption and noted (out of scope) that a readiness-only gate mutant is masked by the reservation-side gate.

Startup-pressure setup: full SQLite 5,982 passed, 38 skipped (232.37 s); full PostgreSQL stopped at 4,751 passed, 29 skipped with one setup failure. Cause: the startup-pressure test prequeued three requests, and under C1 receipt 2 could authorize request 3 before pressure was installed (focused assertion `third request dispatched before pressure installation`, 8.42 s). Correction: queue two initially, acknowledge real external pressure, then add the third before the next turn (no workload, pressure, assertion or deadline change). Positive PG 7.06 s; the startup-window bypass mutant fails the blocked-state assertion after 30 s/960 turns; restored positive 6.70 s. Review: Claude, 86.63 s, **no blocker**; optional comment revision declined. An earlier test review was stopped without verdict because its prompt captured a temporary mutation; it is invalid evidence. Original-load PostgreSQL rerun: 5,934 passed, 29 skipped (338.95 s).

Fresh C1 B-before baseline at `63dd4752` (corrected harness SHA-256 `e581597e…`; 568 raw, provenance, config, control and custody checks passed; separate from the earlier A/B/A harness revision), seconds:

| Backend/mode | Useful elapsed | PING call-to-return | STOP handler return | Receipt-only turns |
|---|---|---|---|---|
| sqlite/quiet | [4.297253833, 4.36699075, 4.314964875] | [0.012259542, 0.015668416, 0.012934625] | [0.012321625, 0.002982208, 0.00268725] | [None, None, None] |
| sqlite/trace | [4.059260417, 3.915023958, 4.107326375] | [0.014435208, 0.018684917, 0.013934208] | [0.003674208, 0.0051205, 0.0041225] | [24, 24, 24] |
| postgres/quiet | [5.281058667, 5.484970083, 5.168253083] | [0.182524083, 0.1839685, 0.171176125] | [0.079221542, 0.0462115, 0.070456417] | [None, None, None] |
| postgres/trace | [7.570949125, 12.515982417, 11.320626542] | [0.204551875, 0.192513709, 0.242457375] | [0.067129917, 0.046938458, 0.091808125] | [0, 0, 0] |

All trace receipt censor counts were zero. The PostgreSQL structural baseline was zero, so C1 needed the quiet elapsed benefit there.

### C1 measurement decision and restoration, 2026-10-28

Rejected C1 after 12 valid batches / 96 scenarios and 1,894 verification checks. PostgreSQL quiet elapsed (seconds): B-before [5.281058667, 5.484970083, 5.168253083]; C1 [5.278710833, 4.867836917, 5.11627925]; B-after [4.843405791, 4.920405875, 4.782946084]. The speed bar fails, and surrounding baseline ranges are disjoint, limiting attribution. PostgreSQL receipt-only counts remain [0, 0, 0] in all arms with zero censoring, so the structural bar also fails. No primary quiet regression met the all-three rule on either backend; this is not equivalence. No additional set was collected to seek a win. An unrelated Docker/Django pytest job overlapped the first trailing SQLite trace batch; the boundary guard stopped measurement, that batch was retained under `excluded-external-overlap/` and excluded, and only it was replaced.

Restored the B Manager, three specs, May lesson and original family-maintenance test file exactly from HEAD; the candidate patch was saved locally. Kept only the independently reviewed pressure-setup repair. Concurrent launch workers remain deferred.

### C2 design review, baseline and promotion (2026-10-28)

| Round | Reviewer / duration | Verdict | Disposition summary |
|---|---|---|---|
| Initial C2 review | Claude, 741.91 s | **BLOCKED** | Replaced the broad first draft with the operative design above (table below). |
| Round 2 | Claude, against `a385f3e8` | **BLOCKED** on one item (exhausted internal quota permits priority inversion) plus timing-dependent pass-2 witnesses | Corrections below. |
| Round 3 | Claude, 455.55 s | **PASS**; no P1/P2; no reconsideration | Four P3 precision refinements accepted; optional [MA-1.8] cross-reference declined (the promoted MA-3 text names MA-1.8 and the bounded discovery delay). |

| Initial-review finding | Disposition |
|---|---|
| F1 aggregate control starvation/churn | Accept. Remove the aggregate control quota and override; use boundary emptiness; keep 33 initial PINGs and exact no-release proof. |
| F2 exact-turn tests | Accept; inventory in the test section above. C1-only tests removed with rejected C1. |
| F3 exact spec deltas | Accept; exact B-baseline strings and replacements. |
| F4 broad safety helper | Accept simplification: no dead publication or empty-map stale checks, repeated child veto or service/timer projections; retain B per-delivery child gate and classification continuation before pass 2. |
| F5 structural budgets | Accept removal of result/control bookkeeping; retain pre-seed attempted-authorization semantics and both lane counters (Manager REQUEUE refills work); reject the blanket dataclass taste claim. |
| F6 first-pass equivalence | Accept correction of the claim; first-pass changes are explicit (lane quotas, pre-seed failed-attempt fence); measure the whole candidate. |
| F7 pressure phase | Accept need for exact phase; new barrier selected. |
| F8 internal topology | Adopt simpler real eligible public topology and forward-observe priority draining. |
| F9 rejected C1 wording | Accept: remove exception, restore B, retire C1-only tests. |
| F10 opportunity evidence | B trace reconstruction already proves 72/72 receipts applied before the launching delivery pass returned; count repeated `queue_dispatch` spans per complete (manager, turn) enclosure for opened extra passes. |

The new bound is 128 aggregate attempted fetches per lane; today's totals were not unchanged (public could do watcher 1 + explicit 128, and internal priority draining could exceed its helper's cap).

| Round-2 finding | Disposition for round 3 |
|---|---|
| Exhausted internal quota permits priority inversion | Shared public prefetch guard; existing pending helper gains explicit unknown policy; real pending, empty and read-failure witnesses. |
| Pass-2 witnesses depend on receipt timing | Bounded real publication barrier; inherited drain applies the actual receipt. |
| Empty drain retains stale retry deadline | Both empty observations clear stalled state before True. |
| Stale C1 test identifier; missing B mapping | Removed; exact spec 03 mapping and spec 05 strict-priority replacements added. |
| Failed receipt / explicit fallback ambiguities | Successful submission with a failed settled receipt can qualify; unhandled fallback publication cannot. |
| Classification frontier freshness | Reuse the indexed frontier under MA-1.8; disclose at most one extra dispatch of discovery delay, with a classification-continuation veto firing test. |
| Pending termination and second-pass pressure | Actual SIGTERM-recording path at the settled boundary; tagged real second-pass usage read on the existing ceiling 80/reserve 0.1 fixture. |

Fresh C2 B-before baseline: four batches at frozen `63dd4752`, unchanged harness, normal 12 workers, PG ceiling 300; 612 raw checks passed. Elapsed seconds:

| Backend/mode | Elapsed samples (seconds) | Receipt-only turns |
|---|---|---|
| sqlite/quiet | [4.494232416, 4.169684458, 4.311773459] | [None, None, None] |
| sqlite/trace | [4.333815334, 4.116365333, 4.760051542] | [24, 24, 24] |
| postgres/quiet | [4.666170583, 5.097863459, 5.051055084] | [None, None, None] |
| postgres/trace | [5.894320167, 5.995223583, 5.821479917] | [0, 0, 0] |

Promoted exactly the four before-runtime replacements to specs 03 and 05 at retained B runtime `a385f3e8`; source mappings and lesson were to follow with runtime.

### C2 implementation, review and verification (2026-10-28 to 2026-10-29)

RED qualification before runtime edits confirmed the intended missing second PG submission, 129th internal/public fetch, forbidden public reservation on unknown internal state, failed-seed successor reservation, missing boundary controls, missing second-pass maintenance reachability and missing boolean empty-control evidence; SQLite batching, checkpoint/start custody, held-worker PING/STOP, SIGTERM and classification cases were keepers.

| Round | Reviewer / duration | Verdict | Findings and dispositions |
|---|---|---|---|
| Runtime fresh-eyes | same-family (authored the pressure test, not runtime) | no actionable defect | — |
| Implementation review (delta against `359d8d8f`) | Claude, 666.25 s | Blocker F1 (test gap); no runtime defect | F1 accept: batch test gets two older public rows and two internal rows; removing source-hint retention must fail. F2 accept: move allowance closure and submission proof into `_launch_child_task`, retiring the new C901 suppression `RUFF-SUP-381`. F3 accept: reclosure scopes only an unused extra grant. F4 accept: record consolidation of fake PING/old STOP tests into held-worker witnesses. F5 accept: map actual owners. |
| Corrected closure (Manager SHA-256 `2706cc17517d533705961e282df72b61f830af98e16d749f1e3d9acf163a6757`) | Claude, 381.70 s; plus same-family fresh-eyes | **No blocker**; F1–F5 and pressure injection closed in source | N1 (P3) accept as an evidence condition: periodic inactive-queue rediscovery can rescue a dropped internal hint, so a retention-mutant pass must be classified by timing, not repeated until failure. N2 accept: pressure instruction and retention mutant listed. N3 accept: duplicated no-attempt wording removed. |
| Focused test review (completed-pass counting) | Claude, 72.80 s | **No blocker** | O1 seed-path mutation not added (seed charging has its own fault mutation); O2 accepted (SQLite exercises compatibility, not PG-only sensitivity); O3 observer lifetime unchanged. |

Pressure witness refinement (operative): tag the actual second public `_admission_allows_queue` call only while the same owning `process_once` is active and the first receipt has been applied; at that boundary install real external connections through the held actor (target U = 74), enqueue the late internal request after priority selection, require premises (tag fired, child-ready barrier, pre-pressure U below the fast-path floor, actor acknowledgement, 72 ≤ U < 80) before the exact historical reservation oracle, and keep the fresh-read assertion after custody so a cached-read mutant fails on forbidden custody. Removing extra-pass reclosure must let tail convergence reserve the late internal request (proving the ordinary-grant scope, not necessarily a third authorization).

Verification:

- Pre-correction: SQLite focused 40 passed (2 PG-only skipped); full SQLite 5,981 passed, 65 skipped (303.56 s); mypy (480 files), Ruff, format and index passed; 11 Docker parity skips were unverified; PostgreSQL startup never reached pytest because local Docker requests hung.
- Review corrections: 355 targeted SQLite tests passed, 5 PG-only skipped. Nine SQLite mutations each failed at the intended assertion with exact restored GREEN (SQLite pass 2, internal-cap omission, public-cap omission, public-cap reset at fallback, pending-internal priority omission, unknown-internal fail-open, delayed pre-seed charge, retained post-handle stalled state, unconditional public denial despite known-empty internal quota).
- Corrected full SQLite: 5,981 passed, 65 skipped (319.85 s); full mypy (480 files), Ruff, format (633 files) and suppression-index reconciliation passed.
- PostgreSQL recovery after Docker was restored: candidate PG correctness 48 passed. The checkpoint `submitted_in_pass=False` mutant initially **survived** (1 passed): PostgreSQL capacity 0 means automatic server capacity, not disabled admission, and `_handle_child_launch_failure` schedules a real retry, so a wrongly opened extra pass still could not reserve the successor. The failure test was extended to require exactly one completed `queue_dispatch` phase in its first turn (MF-6/MA-3 forbid another complete pass without successful submission); no safety guard was removed.
- All 13 PostgreSQL mutations then failed their intended assertions with same-node GREEN: third pass; checkpoint/start false-submission completed-pass violations; missing control drain, cap falsely empty, pending SIGTERM, classification continuation, sentinel, deadline, cleanup scope, source-hint retention (rediscovery did not mask it), cached public usage and unused-extra-grant tail reclosure.
- Full PostgreSQL: 5,946 passed, 29 skipped (351.97 s), normal 12 workers, native 300.
- Measurement-provenance audit: `pyproject.toml`, `uv.lock` and `bin/pytest-pg` pinned and matching frozen B. Limitation: historical B-before recorded SimpleBroker source/Python/arguments but not simplebroker-pg/psycopg fingerprints or working-tree lock bytes; the comparison is reported with that qualification and never supports an unqualified causal claim.

### C2 measured decision: reject batching (2026-10-29)

The complete B-before/C2/B-after comparison finished: 12 batches, 96 scenarios, 2,038 verification checks. PG quiet elapsed seconds: B-before [4.666170583, 5.097863459, 5.051055084], C2 [4.852761583, 4.619805625, 4.748111625], B-after [4.924441791, 5.054709208, 4.852210416]. Only one of three candidate repetitions beat the fastest surrounding baseline (4.666170583); the required all-three criterion failed. No primary quiet metric had a reproducible regression.

The strongest counterargument is the consistent PG PING improvement: candidate [0.145752708, 0.161187625, 0.153644292] seconds, each below every surrounding baseline sample. This does not replace the predeclared elapsed-time criterion. Batching was exercised: measured throughput traces had [16, 14, 14] extra spans and [8, 10, 10] extra authorizations; maximum simultaneous creators remained one. Receipt-only PG eligibility remained zero. The dependency-provenance qualification applies. This is not proof of equivalence or that batching cannot help.

**Decision:** do not use the optional additional comparison to seek a favorable result. Reject C2 and restore B runtime/spec behavior (`359d8d8f`), retaining independently useful real-broker test replacements and the startup-pressure fixture repair. The reviewed C2 candidate is recoverable at commit `94606277`. B duplicate-sweep removal remains retained for eliminating verified redundant work, without a speed claim. Concurrent launch workers remain deferred (the D gate was not met).

### Retained B test review and simplification (2026-10-29)

Cross-family Claude review, 481.32 s: **no blocker**. Removed-test replacements assessed: the held-worker `held[PING]` witness is stronger than the fake-process PING test; `serial_dispatch` turn 1 and cross-turn order are real replacements for the two fake-launch tests; `held[STOP]` plus existing `test_manager.py` STOP tests are adequate together. The control-drain rewrite (fault at `Queue.delete` returning False) is an improvement.

| ID | Finding (summarized) | Disposition |
|---|---|---|
| F1 [P3] | `maximum_creators` cannot fail in either test. | Accept: remove counter and accounting; exact reservations and receipts remain the evidence. |
| F2 [P3] | STOP-specific checks are weak on SQLite. | Accept as-is: STOP state plus PG successor custody cover it. |
| F3 [nit] | Leftover C2 count/names. | Accept. |
| F4 [nit] | Duplicated fetch/apply wrappers. | Defer: ten lines do not justify a wider helper API. |
| F5 [nit] | Comment misattributes stalled-state clearing. | Accept: attribute it to the subsequent empty peek. |
| O1 | STOP can finish draining while a launch result is outstanding (pre-existing). | Observation for the owner; no runtime change authorized by this review. |
| O2 | Indirect helper import in the PG system test. | Accept: import from `tests.helpers.manager_dispatch`. |
| O3 / O4 | Test cadence harsher than production; optional custody expansion. | No defect; not added in this slice. |

An earlier native retained-delta review reported no findings but disclosed prior authorship of candidate tests; the cross-family pass supplies independent validation. Full local SQLite on restored B plus retained tests and CI-D2: 5,987 passed, 51 skipped (296.31 s) at normal 12 workers.

### Retained-test final review closure (2026-10-29)

Author-independent same-family fresh-eyes: no actionable finding. Cross-family focused closure (Claude, 92.76 s): **no blocker**; F1, F3, F5 and O2 closed; three optional cosmetic observations declined. Targeted final SQLite 29 passed (23.63 s); full mypy (480 files), Ruff and format (633 files) passed. Final retained-tree PostgreSQL suite: 5,938 passed, 29 skipped (408.61 s) at normal 12 workers, native 300, CI diagnostics enabled; source manifest matched the tested/reviewed files. The preceding full SQLite run (5,987 passed, 51 skipped, 296 s) remains the full production-tree verification. No C2 runtime/spec delta remains.

### Final-head CI result and retained evidence (2026-10-29)

Head `b4afe36e1c56ce4c3448b0b4fff69563e69221fb`, Actions run `40890367466`, completed with failure. All 27 OS/Python jobs, PostgreSQL CLI/remaining, lint and coverage passed. PostgreSQL core-commands job `123505625501` failed during setup of `test_manager_services_control_while_real_launch_receipt_is_held[PING]`: the connection to the isolated server on port 32781 was rejected with `FATAL: the database system is starting up`, before any PING assertion. Four dependent extension/integration jobs were skipped, not passed. No rerun or behavior change.

Retained artifact `postgres-weft-pg-test-2962-25cf2916.log` showed the container running, exit 0, `OOMKilled=false`; its logs show normal temporary initialization-server shutdown, final server starting at 14:30:25.052 UTC, the rejected connection at 14:30:25.064 UTC, and final ready at 14:30:25.070 UTC (6 ms later). This is startup-boundary evidence, not an OOM/crash attribution; readiness then combined host TCP accept with container-local psql over the Unix socket. It does not retroactively explain the earlier Windows and PG failures. The repair is CI-D3 in `2026-10-27-runtime-and-test-repair-plan.md`.

### Closure (2026-10-29)

Closed as `completed` by `2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md` Task 4. The branch head after the CI-D3 readiness repair, `540707a370d32cf9475832099fabd52fa1dc7f0a` (Actions run `40902305487`), completed SUCCESS on all 36 jobs: 27 OS/Python matrix jobs, 3 PostgreSQL slices, lint, coverage and the 4 dependent Django/Docker/macOS sandbox/microsandbox jobs; no skipped job was counted as a pass (recorded in the runtime repair plan).

Final state: B (`_dispatch_cleanup_scope` duplicate-sweep removal) is retained without a speed claim; C1 and C2 are rejected; concurrent launch workers (D) are deferred because the D gate was not met. The temporary 1,722-line measurement harness `tests/system/test_manager_dispatch_costs.py` (and its `RUFF-SUP-379`/`RUFF-SUP-380` suppression groups) was retired by `2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md` after Arm B was selected and C1/C2 were rejected, because the experiment is complete; the full harness is recoverable at commit `4a98f26b`. The harness commands in the Verification section above therefore describe the historical experiment, not current gates.
