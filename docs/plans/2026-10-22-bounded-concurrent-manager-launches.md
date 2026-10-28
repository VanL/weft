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

Record per arm: useful completions and elapsed time; enqueue-to-start and completion-to-next-eligible-start distributions; PING/STOP observation latency; turns and authorizations per turn; sweep calls per authorization; inclusive/exclusive phase times with nesting declared; actual admission observations and checkpoint publications; worker-lane and startup membership maxima; global observed pressure; and time blocked by pressure, startup exposure, active launch, or uncertain custody. Use existing timing hooks and observer-only test wrappers. Preserve causal order and IDs; if block-reason intervals require instrumentation, keep it test-local unless an independently justified production diagnostic is needed.

Keep B only with the scope/freshness proofs even if timing gain is small: removal of the redundant nested lifecycle sweep is independently meaningful. Keep C1/C2 only when repeated measurements show fewer redundant turns or meaningful latency/completion gain without control, custody, or maintenance regressions. Do not hardcode machine-specific throughput percentages into correctness tests. Record a no-benefit result and remove an unhelpful C2 loop rather than leaving dormant switches.

Concurrent-worker gate: after choosing the smallest serial result, show that eligible requests repeatedly wait specifically for process creation, with fresh capacity/startup headroom and no custody/control blocker; measure worker occupancy and the available overlap on supported runtimes. The 101 ms historical process-launch total alone does not satisfy this gate. Report the added failure surfaces and expected bounded benefit. If evidence is insufficient, close the serial work without adding workers. If sufficient, revise this plan or create an indexed successor with exact concurrent custody/ordering deltas, receive cross-family review, promote those deltas, and only then implement concurrency. No automatic jump from C2 to multiple workers.

## Serial Dispatch Design and Maintenance Boundaries

B uses an explicit reactor-owned delivery-scope flag with `try/finally`. Set it only around the existing queue delivery paths after turn-level cleanup completed. `_handle_work_message` skips its sweep only in that scope; direct calls and shutdown keep their cleanup. Clear the scope on exceptions. Do not put a stale per-turn marker on arbitrary later calls, nor use a second selective-cleanup API. Before further delivery, a ready child sentinel (`_child_sentinel_adapter.wait(0)`), a due child terminal retry, or newly due/unserviced safety maintenance ends the slice and schedules an immediate ordinary turn, which drains and settles it. A wake hint is not process-death evidence. Turn-start classification/source repair services a bounded page, not the whole backlog: its remaining continuation must not veto the normal first eligible dispatch after that maintenance. It may end additional C2 passes and must retain its immediate continuation wake. Distinguish already-serviced page continuation from new unserviced safety work explicitly.

C1 removes only receipt consumption of the launch allowance. After applying a receipt through the existing commit/handoff owner, process controls and enforce ownership, startup and custody gates before another reservation. A receipt whose checkpoint failed cannot open the lane for ordinary work. Keep `_resume_startup_dispatch` source activation and local wake: receiving a result near the end of a turn must not strand ready work when no further pass occurs.

C2, if retained after measurement, is an outer bounded repetition around existing delivery passes, not a new queue-selection implementation. A pass returns fully before any receipt is committed or another pass begins. Reuse the existing BaseTask pre/post-pass result drains; make their aggregate result budget explicit across the outer turn. Count authorization attempts at the final pre-seed fence, including attempts whose seed/checkpoint/start subsequently fails. Do not count a receipt as authorization.

There is no existing aggregate watcher work counter to share: `_drain_priority_queues` can drain its highest non-PEEK priority until it stops making progress, outside the explicit Manager drain helper's `max_messages` loop. For C2 add Manager-owned attempted spawn-delivery counters, reset only at outer-turn entry, using `MANAGER_INTERNAL_SPAWN_DRAIN_MAX_MESSAGES` and `MANAGER_PUBLIC_SPAWN_DRAIN_MAX_MESSAGES` as the lane ceilings. At `_process_queue_message`, check/charge the applicable counter before fetch/reservation after ordinary eligibility gates. Count malformed/rejected deliveries and unsuccessful fetch attempts as well as work that reaches launch. Both the inherited watcher pass and explicit public/internal drains must pass that gate; no reset on source reactivation or another pass. Exhaustion suppresses that lane for the rest of the turn and preserves a fresh-turn continuation when pending work remains. Keep the generic watcher unchanged. Authorization and attempted-delivery budgets answer different questions; neither replaces resource or custody checks.

C2's aggregate result/control bookkeeping must include Manager calls outside the BaseTask pass as well as the pre/post-pass calls. If the remaining result budget is zero, skip `_drain_worker_results` entirely: its `max_results=0` currently still permits one result. Do not repeatedly call it with zero or reset the count through a helper. Retain ordinary cleanup/direct-call behavior outside the bounded reactor slice.

At a safe boundary, first yield for pending sentinel/publication or unserviced due safety work needing normal turn processing. A classification/repair continuation already serviced at turn start does not forbid the first ordinary dispatch; yield for its remaining page work only before extra C2 passes. Apply a ready launch result only through the normal result drain, outside queue delivery; then process bounded controls and check the gates again. If the one lane still has an active/unresolved request, return to the existing arbiter immediately. No `join`, sleep, spin, receipt timeout, recursive task turn, or second service group is permitted. If another receipt is already handled and the lane is free, one further existing delivery pass may run within the remaining authorization/work budgets. Do not restart per-pass drain budgets. Once the budget is spent, request a normal continuation only if there is actionable work; unchanged blocked backlog must not spin.

Use existing deadline owners, not a second set of clocks. `next_wait_timeout()==0` is not by itself a maintenance-due predicate because it includes actionable work. If a pure deadline aggregation helper is needed, extract it locally and use it from both the existing timeout method and the slice gate. Preserve due service convergence, registration, classification/repair continuation, leadership rechecks, and child terminal maintenance. `_family_classification_pending` after a full page is a continuation request, not proof that known work is ineligible. Track whether its bounded opportunity has already run this turn; neither every zero timeout nor every pending continuation is a launch veto. If observing readiness requires destructive queue reads or recursive lifecycle mutation, stop and revise C2 rather than adding a second scheduler.

A scalar single-worker active request and receipt-time lineage remain sufficient. Do not pre-register selected lineage, change generic error routing, or add slot maps for the serial optimization. Those costs belong only to a justified concurrency successor.

## Proposed Spec Delta

Promotion strategy A: promote accepted paragraph deltas in active specs before the corresponding implementation, without claiming new implementation mappings. Add mappings and reciprocal docstrings with code. Record each promotion baseline. Measurements of A need no promotion. B promotes the B paragraph; C1/C2 promote only the retained C paragraphs. The current turn rules govern until then. No [MANAGER.18] custody exception is proposed.

### B: `03-Manager_Architecture.md` [MA-3]

Append before the next same-level section:

> Manager queue delivery may reuse child cleanup already performed at that turn's maintenance boundary. The work handler omits its duplicate sweep only within an explicit reactor-owned delivery scope; standalone handler calls and shutdown retain their cleanup. The scope is cleared on failure. Ready child-exit evidence or due terminal maintenance ends delivery and schedules immediate normal settlement before another launch authorization. Lifecycle commits do not recurse into an active queue-delivery pass.

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

1. **Establish a reproducible baseline and cost witnesses.** Read `weft/core/manager.py`, `weft/core/tasks/base.py`, `weft/core/tasks/multiqueue_watcher.py`, `weft/core/tasks/service.py`, `weft/core/launcher.py`, and the current family system test. Extend `tests/core/test_manager_burst_bookkeeping.py` for operation counts and add `tests/system/test_manager_dispatch_costs.py` (new) for the real fixed workload/trace fixture if it cannot fit clearly in the existing file. Reuse the lifecycle pattern of `tests/system/test_family_scheduling.py::family_harness` without changing its existing six/eight-input workload or calling a fixture function directly; use a test-local real harness fixture for the fixed 12-parent diagnostic. Reuse workload functions in `tests/helpers/family_workload.py`, and `WeftTestHarness`. Record A and predeclare comparison metrics. Keep process/broker paths real. Stop if the baseline cannot be reproduced or measurement changes scheduling authority. Measurement tests need no invented failing performance assertion.
2. **Isolate duplicate-sweep removal.** Promote B and record baseline; edit `weft/core/manager.py` and `tests/core/test_manager_burst_bookkeeping.py`, with lifecycle assertions in `tests/core/test_manager.py` / `tests/core/test_admission_recovery.py`. Write RED for redundant sweep count and scope cleanup, plus real exit/terminal-retry behavior. Implement B only; retain one authorization and receipt-consumes-turn. Run A/B comparison. Stop if standalone cleanup disappears, scope survives failure, or known exit/custody evidence is bypassed. Independent review before the next behavior slice.
3. **Measure receipt continuation, then conditional serial batching.** Promote C1, update `weft/core/manager.py` and the named tests; keep service group count and `weft/core/family_scheduling.py` unchanged. Write a real receipt-plus-STOP RED and a receipt-plus-ready-work progression test before changing the allowance. Measure B/C1. Only then evaluate C2 safe-boundary opportunities. If worthwhile, promote C2, add its internal constant to `weft/_constants.py`, and add the Manager attempted-delivery counters plus explicit outer-turn result/control budgets across serial passes. Do not assume the priority watcher already supplies that bound. Prefer no BaseTask API change; if a minimal budget accessor/extraction is required, identify and review it before widening the edit. Measure C1/C2 and choose the smallest supported budget or remove C2. Stop on blocking receipt waits, reentrant lifecycle commits, hidden concurrency, changed custody, or fairness regressions. Review the resulting slice independently.
4. **Decide on concurrency from the serial result.** Use the explicit D gate above. Inspect `launch_task_process` and supported multiprocessing resource-tracker/`set_executable` behavior only if overlap is actually justified. Record keep-serial or a separately reviewed successor scope. This step does not authorize implementing slot maps or multiple workers. A no-go closes that branch without weakening the serial outcome.
5. **Reconcile and close the implemented serial scope.** Update only promoted specs, their implementation mappings, code docstrings, relevant lesson, this plan's evidence/deviations, and `docs/plans/README.md`. Run final backend/runtime checks and independent completed-work review. Close this plan only after the chosen serial implementation and proofs land; plan-authoring completion alone leaves status draft. If concurrency is warranted, identify its indexed successor without claiming that implementation is complete here.

## Acceptance Matrix

Use real queues, exact moves, real process creation and `WeftTestHarness`. For global PostgreSQL pressure use `tests/helpers/pytest_pg_runtime.py::isolated_postgres_server` before harness/target construction; schema isolation does not isolate connection usage. Narrow publication fault injection and barriers around actual process creation/event delivery are allowed. Do not replace the launch pipeline, admission controller, or broker. Barriers prove ordering; machine-specific timing gains are diagnostic measurements.

| Boundary | Observable proof and regression control |
|---|---|
| B scope | A real turn dispatches work after one outer cleanup, with no nested handler sweep; standalone handler and shutdown retain cleanup. Raise inside delivery and prove the scope is cleared. Reinstating the duplicate or permanently suppressing cleanup fails opposite assertions. |
| Ready maintenance | Deliver a real child exit or make a real terminal retry due after the outer sweep but before further authorization. Normal settlement precedes next launch. Use a module-owned clock only for the due-time boundary, never shared stdlib mutation. Suppressing the yield must fail. |
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
