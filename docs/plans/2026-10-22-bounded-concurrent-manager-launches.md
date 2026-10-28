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
| Receipt marks turn allowance consumed | Receipt does not count as a new authorization; controls run after receipt commit before next reservation | Queue STOP before receipt processing and prove next request stays unlaunched |
| One new authorization per turn | Retain for B and C1; experiment with bounded C2 only at safe post-delivery boundaries | No receipt waiting or overlapping process creation; real traces must show batches actually occur |
| Family lineage registered on receipt | Preserve | With serial launch custody, receipt commits precede next selection; no provisional scheduler state is needed |
| Existing bounded individual control/result drain calls | B/C1 retain their call pattern; C2 adds explicit remaining budgets across repeated passes | Do not describe current calls as one shared counter; repeated passes must not reset the new outer budget |
| Priority watcher rejection loop has no aggregate work counter | C2 adds Manager-owned attempted spawn-delivery counters using existing public/internal lane ceilings | Check before fetch/reservation through the shared Manager gate; count malformed/rejected attempts and retain continuation on exhaustion |
| A full classification/repair page retains continuation | Preserve known-work dispatch after that turn-start page; continuation may end extra C2 passes | Do not drain the whole backlog before offering an already-indexed eligible request |
| SQLite startup | Preserve one authorization per turn and one unfinished startup | PostgreSQL batching is not permission to change SQLite semantics |
| Internal priority and reserve | Preserve on each eligible selection | Pending internal work wins at next free lane; no special child lane or new resource class |
| Positive startup/publication wake vs missing evidence retry | Preserve event wake and bounded absence deadline separately | Do not lower fallback timers to hide missing readiness continuation |

No new public configuration, dependencies, broker API, durable format, launch group, selective cleanup API, persistent worker pool, or global admission guarantee is introduced by the serial slices. Quadratic frontier normalization, heap reclamation, TaskMonitor clocks, and unrelated waiter redesign are outside this plan.

## Experiment Arms and Decision Rules

Use the same fixed submitted workload, server capacity, resource configuration, result assertions, and standard pytest worker configuration for each comparison. Keep built-in autostart settings identical within a comparison. Also run the original system workload with its unchanged autostart, concurrency, and capacity settings; the minimal diagnostic is not a substitute for that acceptance. Warm once, then record at least three measured repetitions per retained arm with alternating order where practical. Report spread and raw samples, not just the best run. A correctness failure is not cured by reducing load, increasing timeouts/capacity, or lowering xdist workers.

| Arm | Production delta | Attribution question |
|---|---|---|
| A | Current single lane, receipt consumes turn, duplicate sweep | Reproducible baseline |
| B | A plus dispatch-scoped duplicate-sweep removal only | Does one fewer sweep per dispatched request reduce work without delaying required settlement? |
| C1 | B plus receipt-turn continuation, still one new authorization per turn | Does eliminating the receipt-only turn reduce turns and latency? |
| C2 (conditional) | C1 plus bounded serial post-delivery passes, candidate authorization budgets 2, 4, 6 | Are receipts ready soon enough to share maintenance within an outer turn? Is the extra loop worthwhile? |
| D (deferred) | Concurrent launch workers | Not an implementation arm until the later gate and a fresh reviewed spec/design revision |

Budgets are internal implementation constants in `weft/_constants.py`, not user knobs, resource budgets, or aliases for W. One is the C1 control; select the smallest empirically supported budget. Six is an experiment ceiling inherited from the prior draft, not a proven optimum. C2 may legitimately be rejected because real serial receipts seldom arrive before the safe boundary.

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

B uses an explicit reactor-owned delivery-scope flag with `try/finally`. Set it only around the existing BaseTask delivery and explicit public-drain paths after turn-level cleanup and any forced convergence have completed. `_handle_work_message` skips its sweep only in that scope; standalone handler calls, service convergence and shutdown keep their cleanup. Clear the scope on exceptions. Before each spawn delivery in the scope, including the first, a ready child-sentinel observation (`_child_sentinel_adapter.wait(0)`) or a due child-maintenance deadline from `_manager_child_timeouts` yields to an immediate ordinary turn. That turn drains the sentinel hint and performs normal settlement; the gate does not consume observations or perform child cleanup. Existing sentinel wake and due-deadline readiness suffice; do not add a redundant wake call. Existing later service convergence may settle the yielded child in the same outer turn. Assert nonconsumption at the gate return and ordinary settlement before subsequent authorization, not that the child must survive until turn end; B does not change convergence ordering. Place the check after cheap startup/candidate early returns but before admission and fetch/reservation. A sentinel hint is not process-death evidence. B retains one new authorization per turn and receipt-consumes-turn behavior. Other maintenance owners retain their existing phases and deadlines; no blanket maintenance-due veto is introduced. A bounded classification/source-repair page may retain continuation while known eligible work receives its normal first dispatch; preserve its continuation wake. General due-maintenance checks before extra serial passes belong only to C2.

B's deadline predicate relies on `_cleanup_children` advancing or retiring each child deadline it services. Sentinel-recheck and proof-grace deadlines already do so; S2 makes publication retry do the same. Repair S2 in `2026-10-27-runtime-and-test-repair-plan.md` clears a consumed terminal-publication retry before entering fresh proof grace, while preserving future retries, custody, and failed-publication rearming. It restores existing [MA-1.6a] no-spin behavior and belongs in the common repaired A/B baseline, not in B's measured delta. Include the real clean-exit/read-and-write-outage/recovery regression; a killed wrapper alone does not cover this transition.

C1 removes only receipt consumption of the launch allowance. After applying a receipt through the existing commit/handoff owner, process controls and enforce ownership, startup and custody gates before another reservation. A receipt whose checkpoint failed cannot open the lane for ordinary work. Keep `_resume_startup_dispatch` source activation and local wake: receiving a result near the end of a turn must not strand ready work when no further pass occurs.

C2, if retained after measurement, is an outer bounded repetition around existing delivery passes, not a new queue-selection implementation. A pass returns fully before any receipt is committed or another pass begins. Reuse the existing BaseTask pre/post-pass result drains; make their aggregate result budget explicit across the outer turn. Count authorization attempts at the final pre-seed fence, including attempts whose seed/checkpoint/start subsequently fails. Do not count a receipt as authorization.

There is no existing aggregate watcher work counter to share: `_drain_priority_queues` can drain its highest non-PEEK priority until it stops making progress, outside the explicit Manager drain helper's `max_messages` loop. For C2 add Manager-owned attempted spawn-delivery counters, reset only at outer-turn entry, using `MANAGER_INTERNAL_SPAWN_DRAIN_MAX_MESSAGES` and `MANAGER_PUBLIC_SPAWN_DRAIN_MAX_MESSAGES` as the lane ceilings. At `_process_queue_message`, check/charge the applicable counter before fetch/reservation after ordinary eligibility gates. Count malformed/rejected deliveries and unsuccessful fetch attempts as well as work that reaches launch. Both the inherited watcher pass and explicit public/internal drains must pass that gate; no reset on source reactivation or another pass. Exhaustion suppresses that lane for the rest of the turn and preserves a fresh-turn continuation when pending work remains. Keep the generic watcher unchanged. Authorization and attempted-delivery budgets answer different questions; neither replaces resource or custody checks.

C2's aggregate result/control bookkeeping must include Manager calls outside the BaseTask pass as well as the pre/post-pass calls. If the remaining result budget is zero, skip `_drain_worker_results` entirely: its `max_results=0` currently still permits one result. Do not repeatedly call it with zero or reset the count through a helper. Retain ordinary cleanup/direct-call behavior outside the bounded reactor slice.

At a safe boundary before an additional C2 pass, first yield for pending sentinel/publication or unserviced due safety work needing normal turn processing. A classification/repair continuation already serviced at turn start does not forbid the first ordinary dispatch; yield for its remaining page work only before extra C2 passes. Apply a ready launch result only through the normal result drain, outside queue delivery; then process bounded controls and check the gates again. If the one lane still has an active/unresolved request, return to the existing arbiter immediately. No `join`, sleep, spin, receipt timeout, recursive task turn, or second service group is permitted. If another receipt is already handled and the lane is free, one further existing delivery pass may run within the remaining authorization/work budgets. Do not restart per-pass drain budgets. Once the budget is spent, request a normal continuation only if there is actionable work; unchanged blocked backlog must not spin.

For C2's additional passes, use existing deadline owners, not a second set of clocks. `next_wait_timeout()==0` is not by itself a maintenance-due predicate because it includes actionable work. If a pure deadline aggregation helper is needed for those additional passes, extract it locally and share it with the existing timeout method. Preserve due service convergence, registration, classification/repair continuation, leadership rechecks, and child terminal maintenance. Distinguish the normal first dispatch after a serviced bounded classification/repair opportunity from additional C2 passes: remaining continuation may end extra passes but must not require exhaustion of the backlog before that first dispatch. B uses only the existing child readiness/deadline projection and leaves other maintenance phase order unchanged. If C2 readiness requires destructive queue reads or recursive lifecycle mutation, stop and revise C2 rather than adding a second scheduler.

A scalar single-worker active request and receipt-time lineage remain sufficient. Do not pre-register selected lineage, change generic error routing, or add slot maps for the serial optimization. Those costs belong only to a justified concurrency successor.

## Proposed Spec Delta

Promotion strategy A: promote accepted paragraph deltas in active specs before the corresponding implementation, without claiming new implementation mappings. Add mappings and reciprocal docstrings with code. Record each promotion baseline. Measurements of A need no promotion. B promotes the B paragraph; C1/C2 promote only the retained C paragraphs. The current turn rules govern until then. No [MANAGER.18] custody exception is proposed.

### B: `03-Manager_Architecture.md` [MA-3]

Append before the next same-level section:

> Manager queue delivery may reuse child cleanup already performed at that turn's maintenance boundary. The work handler omits its duplicate sweep only within an explicit reactor-owned scope covering ordinary queue delivery; standalone handler calls, service convergence and shutdown retain their cleanup. The scope is cleared on failure. Before each spawn delivery in the scope, including the first, a ready child-sentinel observation or a due child sentinel/post-exit maintenance deadline yields to immediate normal settlement. Child-cleanup lifecycle commits do not run inside this ordinary delivery scope. Reusing this child cleanup does not change the normal phases or deadlines of other maintenance owners or make a serviced classification-page continuation a veto on the first eligible dispatch.

### C1: `05-Message_Flow_and_State.md` [MF-6]

Replace exactly the sentence beginning “While a launch worker is active” and ending “in the same turn.” with:

> While the single launch-worker lane has an active request, the Manager continues task-local control and lifecycle processing but does not reserve another spawn request. Applying a prior launch receipt does not consume a new-launch allowance. Before the next reservation, its normal receipt/custody effects and pending controls are processed, and ownership, startup and fresh admission gates are checked again. STOP observed at the final pre-seed control fence prevents authorization; already-authorized work retains custody through seed, checkpoint, worker handoff and settlement. No post-seed STOP requeue/reseed path is introduced.

Clause audit: preserve PING responsiveness, broker-free process creation, reactor-owned broker effects, and exact reserved disposition. Replace the same-turn prohibition only after receipt handling with the explicit serial gate and control fence. Do not import the former draft's multi-worker exception.

In `03-Manager_Architecture.md` [MA-1.8], replace exactly “The next reactor turn reevaluates known work through normal measured admission and custody gates without waiting for inactive-source rediscovery.” with:

> After receipt processing and controls, a safe dispatch boundary may reevaluate known work through normal measured admission and custody gates in the same turn; otherwise an immediate continuation turn does so without waiting for inactive-source rediscovery.

The surrounding startup-source activation, empty-source idling, blocked-backlog suppression, and notification-interest clauses remain unchanged.

### C2 only if retained: `03-Manager_Architecture.md` [MA-3]

Append after the B paragraph:

> A Manager may perform bounded serial dispatch passes within a reactor turn. At most one launch request occupies the launch-worker lane. PostgreSQL's internal per-turn authorization budget is finite and no larger than six; SQLite retains one. An authorization attempt consumes budget before inbox seeding, even if a later launch phase fails. Receipts do not consume authorization budget. Separate Manager-owned attempted spawn-delivery budgets apply before fetch/reservation across both watcher and explicit drain paths, using the existing public/internal lane ceilings and counting malformed or rejected requests. Result and control budgets cover their calls across the entire outer turn. Budgets reset only at outer-turn entry; exhaustion preserves actionable continuation for a fresh turn.
>
> Receipt commits occur only after a delivery pass returns. A busy or unresolved lane, a ready lifecycle fact requiring maintenance, newly due/unserviced safety work, exhausted budget, STOP, or a closed correctness gate ends the slice. A turn-start bounded classification/repair page does not have to exhaust its backlog before known eligible work may receive the normal first dispatch opportunity; its remaining continuation may end additional passes and remains scheduled. The reactor never waits for a receipt in order to fill a batch. Startup exposure, fresh per-request admission, and the receipt-before-next-ordinary-dispatch custody requirement remain independent gates.

Record the selected internal budget and measured reason in implementation notes when code lands; do not claim six is the operating value before measurement. Update `_resume_startup_dispatch`'s receipt-consumes-turn comment and the May lesson with the preserved control-order reason in the implementation slice. No change to the program theory is required.

## Dependency-Ordered Slices

Before the first implementation edit, record answers to these comprehension checks: (1) Can a free lane authorize work? Expected: no, resource/startup/ownership/custody and control gates remain. (2) Can a serial batch wait for a receipt? Expected: no, return to the arbiter; a small or empty batch is valid. (3) Can missing execution evidence justify reseeding? Expected: no. (4) Does suppressing the nested sweep remove settlement? Expected: no, ready evidence yields to the normal maintenance owner. Wrong answers require re-reading.

1. **Establish a reproducible baseline and cost witnesses.** Read `weft/core/manager.py`, `weft/core/tasks/base.py`, `weft/core/tasks/multiqueue_watcher.py`, `weft/core/tasks/service.py`, `weft/core/launcher.py`, and the current family system test. Extend `tests/core/test_manager_burst_bookkeeping.py` for operation counts and add `tests/system/test_manager_dispatch_costs.py` (new) for the real fixed workload/trace fixture if it cannot fit clearly in the existing file. Reuse the lifecycle pattern of `tests/system/test_family_scheduling.py::family_harness` without changing its existing six/eight-input workload or calling a fixture function directly; use a test-local real harness fixture for the fixed 12-parent diagnostic. Reuse workload functions in `tests/helpers/family_workload.py`, and `WeftTestHarness`. Record A and the predeclared comparison metrics, baseline spread, and repeatability criterion before B. Keep process/broker paths real. Stop if the baseline cannot be reproduced or measurement changes scheduling authority. Measurement tests need no invented failing performance assertion.
2. **Isolate duplicate-sweep removal.** Promote B and record baseline; edit `weft/core/manager.py` and `tests/core/test_manager_burst_bookkeeping.py`, with lifecycle assertions in `tests/core/test_manager.py` / `tests/core/test_admission_recovery.py`. Write RED for redundant sweep count and scope cleanup, plus real exit/terminal-retry behavior. Implement B only; retain one authorization and receipt-consumes-turn. Run A/B comparison. Stop if standalone cleanup disappears, scope survives failure, or known exit/custody evidence is bypassed. Independent review before the next behavior slice.
3. **Measure receipt continuation, then conditional serial batching.** Promote C1, update `weft/core/manager.py` and the named tests; keep service group count and `weft/core/family_scheduling.py` unchanged. Write a real receipt-plus-STOP RED and a receipt-plus-ready-work progression test before changing the allowance. Measure B/C1. Only then evaluate C2 safe-boundary opportunities. If worthwhile, promote C2, add its internal constant to `weft/_constants.py`, and add the Manager attempted-delivery counters plus explicit outer-turn result/control budgets across serial passes. Do not assume the priority watcher already supplies that bound. Prefer no BaseTask API change; if a minimal budget accessor/extraction is required, identify and review it before widening the edit. Measure C1/C2 and choose the smallest supported budget or remove C2. Stop on blocking receipt waits, reentrant lifecycle commits, hidden concurrency, changed custody, or fairness regressions. Review the resulting slice independently.
4. **Decide on concurrency from the serial result.** Use the explicit D gate above. Inspect `launch_task_process` and supported multiprocessing resource-tracker/`set_executable` behavior only if overlap is actually justified. Record keep-serial or a separately reviewed successor scope. This step does not authorize implementing slot maps or multiple workers. A no-go closes that branch without weakening the serial outcome.
5. **Reconcile and close the implemented serial scope.** Update only promoted specs, their implementation mappings, code docstrings, relevant lesson, this plan's evidence/deviations, and `docs/plans/README.md`. Run final backend/runtime checks and independent completed-work review. Close this plan only after the chosen serial implementation and proofs land; plan-authoring completion alone leaves status draft. If concurrency is warranted, identify its indexed successor without claiming that implementation is complete here.

## Acceptance Matrix

Use real queues, exact moves, real process creation and `WeftTestHarness`. For global PostgreSQL pressure use `tests/helpers/pytest_pg_runtime.py::isolated_postgres_server` before harness/target construction; schema isolation does not isolate connection usage. Narrow publication fault injection and barriers around actual process creation/event delivery are allowed. Do not replace the launch pipeline, admission controller, or broker. Barriers prove ordering; machine-specific timing gains are diagnostic measurements.

| Boundary | Observable proof and regression control |
|---|---|
| B scope | A real turn dispatches work after one outer cleanup, with no nested handler sweep; standalone handler and shutdown retain cleanup. Raise inside delivery and prove the scope is cleared. Reinstating the duplicate or permanently suppressing cleanup fails opposite assertions. |
| Ready maintenance | Deliver a real child exit or make a real terminal retry due after the outer sweep but before the turn's next spawn delivery, including the first. Normal settlement precedes next launch. Use a module-owned clock only for the due-time boundary, never shared stdlib mutation. Suppressing the yield must fail. |
| C1 continuation | Commit a prior real receipt with available capacity and ready source; controls run before the next reservation and one new launch can occur that turn. Restoring receipt consumption fails the progression witness. Queue STOP before that boundary and prove the next request remains unlaunched. |
| C2 batch bound | With already-ready real receipts at post-pass boundaries, record at most the selected number of authorizations and eventual continuation for excess work. Holding the real worker before completion proves the second request stays unreserved and PING is answered. Both removing the bound and waiting/overlapping workers must fail their respective witnesses. |
| Fresh admission and custody | Change real pressure between serial authorizations; the next request stays in source. Unknown usage denies. Fail pre-launch checkpoint: no worker; fail post-launch checkpoint: no next ordinary launch. Restore evidence and prove wake/progress. Cached positive admission or skipped checkpoint gating must fail. |
| STOP and seed | Queue STOP before selection, before receipt handling, and during seed/checkpoint. Pre-seed STOP returns only positively unlaunched exact work; post-authorization STOP does not requeue/reseed it. Verify a child side effect occurs once, including successor handling of remaining work. |
| Selection and internal priority | Check FIFO independent families, round-robin paths, LIFO descent and oldest-sibling sharing using real reservation order, not receipt timestamps. At each free lane an eligible internal request takes its existing priority. No new provisional lineage. |
| Budgets and fairness | C2's Manager attempted-delivery counters cover inherited priority passes and explicit drains. Queue one more malformed internal request than its lane ceiling plus a pending control row to activate the priority topology; only the budgeted attempts occur this turn and remaining work resumes next turn. Repeat for public rejection and mixed source paths. Reverting the pre-fetch gate must fail. Account for Manager and BaseTask result/control calls; an exhausted result budget skips the drain call instead of passing zero. Standard-load PING/STOP, reaps and convergence stay responsive. |
| Classification continuation | Put more than one classification page in source (the reviewer reproduced the state with 65 valid rows). After one real bounded page, a known eligible candidate must still receive its first dispatch opportunity even while `_family_classification_pending` is true and a continuation timeout is zero. Preserve immediate continuation; yield before extra C2 passes for that page work. Converting every pending/zero-timeout condition into a pre-dispatch veto must fail. Newly ready exits and due terminal retries still force normal settlement. |
| Wake and SQLite | Blocked backlog causes no unchanged-work spin; completion/publication wakes normal dispatch without a new timer. Final-pass receipts retain a continuation wake. SQLite still permits one authorization per turn and its existing startup bound. |
| Recovery and cleanup | Preserve existing exact custody crash-point, missing-result, late-result self-reap and STOP deadline tests. B/C do not alter the checkpoint shape or add concurrent obligations. Existing detached-task survival remains. |

Retain the control/custody assertions in `test_manager_reactor_answers_ping_while_child_launch_is_active`, `test_manager_launch_worker_success_commits_once_on_main_thread`, `test_manager_stop_mid_handler_requeues_reserved_work_unlaunched`, `test_manager_late_child_launch_self_reaps_after_cleanup_deadline`, and `test_unknown_launch_custody_is_durable_and_cannot_requeue_or_spin`; update only obsolete receipt-turn expectations.

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

No runtime implementation or spec promotion is claimed by this revision.

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
