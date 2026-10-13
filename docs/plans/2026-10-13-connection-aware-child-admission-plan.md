# Connection-Aware Admission and Child Progress

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.8]; docs/specifications/07-System_Invariants.md [MANAGER.16], [MANAGER.18], [OBS.6]; docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-5], [MF-6]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/14-Python_API_Surfaces.md [PY-1]
Superseded by: none

Class: 5. Admission policy, spawn routing, and runtime connection ownership change. Hardening applies because this crosses the Manager/worker/broker execution path.

Plan type: implementation with spec revision. Implementation authorized by the owner on October 13 after evaluation of the released SimpleBroker prerequisite. This authorization includes dependency floors and spec promotion, but not release or deployment.

## Goal and outcome checklist

October 13 follow-up: the crash-recovery and bounded-refresh revision below
supersedes the earlier terminal-finalization requirement and per-decision
snapshot refresh. Other admission, routing, and rollout decisions stay intact.

Account for connections that accepted launches have not opened yet, and prevent a burst of root tasks from consuming the capacity needed by their leaf children. Keep scheduling in Weft and connection observation in SimpleBroker. Preserve the durable submission and result paths.

- [x] Pending launches consume their full prospective connection budget before launch.
- [x] Opening an already-budgeted connection replaces prospective cost, rather than being counted twice.
- [x] A live task retains room for connections it can open later, including during a child-result wait.
- [x] Root backlog cannot block a leaf child from using reserved child capacity.
- [x] Internal services, control, cleanup, and launch recovery retain their existing owners.
- [x] Real PostgreSQL parent/child execution proves progress without parent timeout or connection rejection.

## Spec baseline and evidence

Baseline: Weft `5f588e9ddc7c6d1f2141d9c645725a70382006ef` (`v0.9.109`) for all source specs and code cited here. SimpleBroker source inspected at `e8573fbb0dfa1126fef11a794b1214505693fb42`. Promotion baseline: record the spec diff base and exact changed files in the spec-promotion slice, then the SHA when committed.

The October 13 ops burst supplies the motivating evidence, not a benchmark promise:

- 154 enrichment submissions in 36.247 seconds; the rollup finished in 44.336 seconds. This plan does not restore synchronous child-start handshakes on submission.
- PostgreSQL recorded 1,318 `mm_weft` role connection rejections. The role limit was 300; the server maximum was 500. Installed admission configuration was `N=300`, reserve fraction `0.1`, giving the public threshold 270.
- 66 enrichment parents remained active during the long child-start gap. All 30 timed-out children eventually completed: they started 1.7–12.3 seconds after their parents failed and ran for 19.5–29.3 seconds.
- Example: child `1791893075641188352` was submitted at 12:04:35.641 UTC; parent `1791893046625878016` failed at 12:16:36.163; the child started at 12:16:38.415 and completed at 12:17:07.632.
- Admission logging was off. These facts strongly support connection starvation but do not recover the historical per-decision connection count or categorically exclude an abnormal launch-worker hang. Do not claim that 66 parents were measured to hold exactly 264 connections.

The defects are independently visible in code: PostgreSQL admission counts only already-open connections; `launch_task_process()` returns after `Process.start()`; public child submission does not carry runtime caller identity and joins the root FIFO. The Django connection is already closed before the result wait. Removing client-session reuse is not the fix.

## Source documents and current structure

Read `AGENTS.md` and the required agent-context sequence, especially `runbooks/writing-plans.md`, `hardening-plans.md`, `review-loops-and-agent-bootstrap.md`, `runtime-and-context-patterns.md`, and `testing-patterns.md`.

| Owner / files | What exists and what to reuse |
| --- | --- |
| `weft/core/admission.py`; `weft/_constants.py` | Pure lane decision and drain-wait policy; one-second denial retry, 30/180-second diagnostic assessment. Extend these, not a second scheduler. |
| `weft/core/manager.py` | Pre-reservation admission, source/reserved queue pairs, tracked launches/children, sentinel reaping, leadership and source suppression. Reuse these owners. |
| `weft/core/spawn_requests.py`; `weft/commands/submission.py`; `weft/client/_client.py` | One canonical request writer and retained submission session. Routing belongs at the shared writer, not in Django or one client method. |
| `weft/commands/_spawn_submission.py` | `_spawn_reconciliation_static_queue_specs`, `_reconcile_submitted_spawn_once`, `_find_reserved_spawn_request_queue`, and `_reserved_spawn_request_queue_names` discover accepted queued/reserved work. Extend these actual consumers to the new pair. Do not resurrect legacy submission rollback deletion. |
| `weft/core/launcher.py`; `weft/core/runners/host.py` | Wrapper entry and function/agent worker entry have the actual TaskSpec TID before executing application code. Install runtime identity here. |
| `weft/context.py`; proposed `weft/core/runtime_identity.py` | Context construction preserves explicit broker/config custody. One small private runtime-identity module serves wrapper, worker, context, and submission code. Do not create a public scheduling API. |
| `weft/core/task_state.py`; `weft/core/tasks/base.py::BaseTask._build_tid_state_payload`, `BaseTask._register_tid_state`; `weft/liveness/policy.py` | Current per-TID runtime evidence and shared liveness probe. Reuse current snapshots and incremental updates; no full history replay per admission. |
| `weft/commands/_result_wait.py`; `weft/core/queue_wait.py` | Retained result session and shared native listener. Result semantics and session reuse stay unchanged. |
| SimpleBroker `extensions/simplebroker_pg/simplebroker_pg/{connections.py,_sql.py,runner.py,plugin.py,__init__.py}` | Public four-integer `get_connection_stats()` and process-local shared pool/listener. The narrow prerequisite below belongs here, not as SQL inside Weft. |

Related plans: `2026-08-25-manager-admission-control-plan.md` and `2026-10-06-admission-controller-plan.md` describe the earlier soft guard and drain diagnostics. They are historical context, not a backlog to complete here. `2026-10-12-burst-submission-complexity-plan.md` describes shipped non-blocking launch behavior that must stay intact.

Comprehension checks before editing: Why is `running` not proof that all future broker connections are open? Why does child priority in the same FIFO fail when an unadmittable root is at its head? Which existing owner can prove a task can no longer open connections?

## Design decision

### 1. Connection budgets, reconciled with actual use

For one PostgreSQL observation, let `O` be total observed connection pressure and `o[t]` the connections attributed to task TID `t`. Each admitted live or pending task has budget `B[t]`. Use:

```text
accounted = O + sum(max(0, B[t] - o[t]) for t in admitted_tasks)
admit candidate only if accounted + candidate_budget <= lane_limit
```

The new task contributes its budget before `Process.start()`. Opening connections then transfers cost from the deficit into `O`. Closing connections while the task is live restores the deficit, because the task may reopen them. Actual use above a budget stays fully counted in `O`. This is one usage calculation, not independent counters that both charge the same connection.

Start with one operator setting, `WEFT_ADMISSION_TASK_CONNECTION_BUDGET`, a positive integer defaulting to **8**. Eight covers the standard host wrapper plus one worker, each with SimpleBroker's default maximum of three pooled connections plus one listener. It is a conservative budget, not a promise that every task opens eight connections or a limit imposed on arbitrary application code. It applies to every new task launch, including internal launches; the already-running Manager is included in observed usage. Store the selected budget with the accepted launch's runtime evidence so a Manager configuration change does not shrink an older task's obligation.

Retain the budget until existing process/liveness/recovery evidence proves the task and its owned worker tree cannot open further connections. A terminal log alone, `Process.start()` return, first mapping, `running`, or elapsed startup grace is not that proof. Deduplicate by full TID. Do not add a broker permit table, lease service, or per-connection token protocol.

### 2. One child lane and one child-sized headroom

Retain `weft.spawn.requests` for root work and `weft.spawn.internal` for existing internal work. Add `weft.spawn.children` for ordinary submissions from an executing task to the same Weft context. Add the matching Manager reserved queue `T{manager_tid}.children_reserved`, following the existing internal source/reserved pair pattern. All three reuse the same validation, seed, launch, acknowledgement, and recovery path.

For PostgreSQL admission, keep the current reserve calculation `R = max(ceil(N*f), 3 + int(liveness_monitor_enabled))`. Let `B` be the configured task budget:

```text
root limit     = max(0, N - R - B)
child limit    = max(0, N - R)
internal limit = N
```

Every candidate must fit its complete budget. At `N=300`, `f=.1`, `B=8`, the ceilings are **262 / 270 / 300**. This protects room for at least one ordinary leaf child from root admission without spending the internal reserve. Internal work runs first, then children, then roots, with existing bounded per-turn dispatch/control rules. Child FIFO ordering is sufficient; no priority numbers or dependency graph.

The lane describes **runtime-originated child submission**, not an assertion that a parent is currently waiting. A task can submit several children and wait later. No wait registration, yield protocol, or result-reader change is needed. Use runtime caller identity, not caller-authored `metadata.parent_tid`, to select the lane. Record the real submitter separately from optional application ancestry tags. Submitting to another broker/context remains root work.

The shared writer puts `submitted_by: {tid, context}` in the spawn envelope for an automatically routed child. `context` is the canonical resolved broker/context identity, not a display name. Manager validation requires a well-formed same-context envelope on the child lane, but does not require the parent to remain alive after durable acceptance. This is a trusted-local-process contract, not a hostile-tenant authentication scheme. Public TaskSpec metadata cannot set it. Keep an explicit caller `metadata.parent_tid` tag; when absent, its default becomes the actual submitter TID for a child, with the existing Manager fallback retained for roots.

The Manager adds the system-owned runtime record `admission: {connection_budget, connection_owner, submitter_tid}` to launch transport and its existing `task_spawned` event. `submitter_tid` is null for roots. The dedicated wrapper installs this immutable record in the private runtime identity; host worker transport carries the same record. `BaseTask._build_tid_state_payload()` includes it in every existing task-state snapshot. It is not a new TaskSpec `spec`/`io` field, mutable caller metadata, or separate queue. Owner labels must distinguish full TID plus broker/context, fit the upstream label limit, contain no credentials, and be identical for a wrapper and its worker. Existing runtime snapshots without this record are addressed by the coordinated drain below, not an invented legacy budget.

Task identity and child routing operate on both backends, even when admission is disabled. Every PostgreSQL launch records its selected budget and owner label even when the gate is disabled, so enabling a gate on a later Manager does not erase live obligations. SQLite uses runtime submitter identity but never imports the PostgreSQL helper or charges the eight-connection model.

Existing pipeline-generated stage/edge work remains on its current internal path. Reclassifying pipelines, whose idle bootstrap can need several simultaneous children, is explicitly not part of this fix. Their connection use still enters the accounting; competing internal work can consume available capacity.

### 3. Bounded guarantee, not a general workflow scheduler

With this Manager's root work within its budgets, stable outside/internal usage, and one ordinary leaf child fitting the child headroom, root saturation cannot prevent queued children from running and releasing waiting parents. The real ops enrichment → LLM workload has this shape. Root throughput may be lower than the old unsafe burst; measure total useful completion, not launch rate alone.

This does **not** promise arbitrary synchronous nesting, concurrent joins requiring all siblings to run, or progress despite unrelated clients exhausting PostgreSQL. A descendant that itself waits can occupy the last child budget. Do not conceal this limit, add automatic cancellation of parents, or build cycle detection/cooperative suspension in this slice. Such workloads need a separate design if requested. Admission remains a soft guard against outside connections, user-created extra processes/targets, and concurrent Manager recovery, not a PostgreSQL-wide hard quota.

## Narrow SimpleBroker prerequisite

The prerequisite is available in released **SimpleBroker >=8.6.0** and **simplebroker-pg >=4.6.0**. Pin both floors in `pyproject.toml` and refresh `uv.lock` before consuming the new APIs. The reviewed release baseline is SimpleBroker `f578fc5b173ad612acf5d932e998ab7fe2a9a220` (`v8.6.0`). Weft continues to own all scheduling policy.

1. `simplebroker_pg.bind_connection_owner(label: str) -> None` installs process-wide attribution before the first PostgreSQL pool, listener, direct connection, or inspection. Labels match `[!-~]{1,54}` and must contain no secrets. Bind the same compact full-TID/context-derived label separately in each dedicated wrapper and worker. Binding overrides `application_name` without changing target/session/pool identity. Same-label binding is idempotent; conflicting or late binding raises `RuntimeError`; invalid labels raise `ValueError`. Failed startup seals binding and resource closure does not reset it.
2. `simplebroker_pg.get_connection_usage(queue: Queue) -> ConnectionUsage` returns fresh ordinary dictionaries shaped as `{total: int, by_owner: dict[str, int]}`. One materialized `pg_stat_activity` population supplies both counts through the existing Queue. `total` includes unrelated and uncredited database-attached backends. Only visible client backends with valid labels earn credit; absent owners receive zero credit and never prove death. Keep Manager sampling outside caller-owned transactions, whose activity snapshots may be cached. The existing four-integer `get_connection_stats()` API remains unchanged.

The release was evaluated against the plan and the public [SB-API-14]/[SB-API-15] contracts. Focused real-PG18 upstream verification passed 65 tests; two isolated-server probes skipped in the parallel run. Pool growth, shared-owner processes, listener recreation, transaction custody, and ordinary-role attribution were covered. Raw caller driver connections and multiplexing proxies remain outside the attribution guarantee. No upstream implementation or private bridge is required in this Weft slice.

Rejected alternatives: a guessed short startup timer, releasing credit at `running`, adding the whole budget to observed usage for every live task (permanent double-counting), or `max(observed, modeled)` (misses unrelated use combined with unopened connections). The upstream observation capability buys the exact accounting needed here, not a monitoring framework.

## Invariants, recovery, and rollout

- Preserve the `TaskSpec → Manager → Consumer → TaskRunner → queues/state log` spine, TID/message-ID identity, frozen resolved `spec`/`io`, forward lifecycle states, queue-first acceptance, and task result formats. Never execute children inline as an admission bypass.
- Leave denied requests in their source queues. Reuse one-second retry and early progress wakes; neither denied lanes nor unavailable observations block STOP, cleanup, reaping, leadership, or shutdown. Existing drain-stall diagnostics do not grant capacity or fail work.
- Runtime identity is immutable for a dedicated wrapper/worker process and includes TID plus resolved context/broker identity. It is installed before broker use and is not inferred from ambient user tags. The first supported automatic route is the built-in host function/agent path, including persistent worker entry. CLI calls outside a task, foreign contexts, and command/extension processes lacking the supported identity transport remain root submissions. Do not advertise coverage those paths do not have.
- Carry budget and owner label through existing launch transport, manager `task_spawned` evidence, and task-owned runtime snapshots. Manager reconstruction uses current live/undecidable snapshots, local pending/committed launches, and retained source/reserved launch evidence. Unknown liveness retains debt. Keep ordinary incremental task evidence as the steady-state path; bootstrap/recovery may use the existing strict, generator-based discovery path.
- There is a crash window before durable child evidence appears. Retained manager reservations and manager-authored launch evidence must be consulted, not just child mappings. Reuse existing recovery authority; never delete a request merely to release its budget. Concurrent replacement before evidence converges remains a disclosed soft-guard limit, not a new distributed permit protocol.
- This is a coordinated forward upgrade. Stop new submissions and drain the old runtime's active work before replacing it: old wrappers cannot acquire labels or budgets merely because their Manager restarts. Verify no old-version live tasks or in-flight/reserved launches remain using existing status/runtime evidence; if they cannot drain, stop the rollout and decide forward repair rather than treating missing labels as zero use. Ship the SimpleBroker prerequisite, then Weft, then restart Managers and clients together before new child routing is enabled. Existing unreserved queued roots retain their TIDs and route and receive a budget when the new Manager admits them. Do not count that unadmitted backlog as already committed debt. No historical outbox deletion, request resubmission, database migration, or coded deploy guard is required. Runtime evidence is still excluded from dump/load.
- Rollback: stop new submissions and let current work finish using the new version; verify all new child source/reserved queues are empty before reverting Weft. If they cannot drain, retain the new version and forward-repair. Never downgrade a Manager that cannot see pending child work. Do not add a deploy sentinel or a special rollback command.

## Proposed Spec Delta

Promotion strategy **A**: edit requirement text in existing spec files before code; do not add new implementation-mapping claims until their code and reciprocal references exist. Keep the pre-promotion text authoritative until approval and independent review. Apply the following text in the spec-promotion slice, then record its baseline identifier. The plan text becomes historical review material after promotion.

### `03-Manager_Architecture.md` [MA-1.8]

Replace the opening capacity paragraphs and PostgreSQL observation paragraph through “permit” with the following. Retain the existing SQLite observation paragraph, denial/retry, exception, control, leadership and diagnostic rules; update “both lanes/sources” in those rules to “all configured lanes/sources”.

> A configured Manager checks capacity before reserving a spawn request. Unset or zero `WEFT_ADMISSION_MAX_CONNECTIONS` disables admission. Reserve fraction remains finite in `[0,1)` and defaults to `0.1`; reserve `R` is `max(ceil(N*f), 3 + int(liveness_monitor_enabled))`. SQLite retains its existing live-TID usage calculation and existing public/internal limits; ordinary child requests use its public capacity while retaining child-before-root dispatch order.
>
> PostgreSQL uses one coherent backend observation containing total usage `O` and usage `o[t]` attributed to each admitted task. `WEFT_ADMISSION_TASK_CONNECTION_BUDGET` is a positive integer, defaults to eight, and supplies the budget recorded for each new task launch. Accounted usage is `O + sum(max(0, B[t] - o[t]))` over distinct admitted TIDs that remain live, pending, or undecidable. Actual over-budget and unrelated connections stay counted in `O`. A candidate admits only when its whole budget fits: `accounted + candidate_budget <= lane_limit`.
>
> PostgreSQL root, ordinary-child, and internal ceilings are respectively `max(0,N-R-B)`, `max(0,N-R)`, and `N`, where `B` is the current configured task budget. A lane whose remaining capacity is smaller than the candidate budget cannot admit it. The root ceiling protects one child-sized headroom independently of the internal reserve. All launched task kinds use this accounting; existing internal pipeline routing is unchanged.
>
> Add the candidate's obligation before launching it. Opening a connection replaces prospective cost rather than adding another charge. Closing connections while a task is live restores its deficit. Keep each obligation until existing process-tree or recovery evidence proves the task can open no more connections; startup return, a running state, or a terminal event alone is insufficient. Preserve selected budget/owner identity through launch evidence and task runtime snapshots. On reconstruction, include pending/reserved launches and manager launch evidence as well as live or undecidable task snapshots. Count each TID once. Use existing recovery and liveness authority, not a new permit store or expiry rule.
>
> This protects leaf-child progress against root saturation within the configured budgets. It does not guarantee arbitrary nested synchronous dependencies, all-siblings-at-once joins, or progress when other clients/internal work consume the available capacity. Application-created processes, additional targets, and concurrent recovery can exceed the modeled budget. Observation and launch are not globally atomic; the policy remains a soft guard, not a server-wide connection lease. Unknown observations fail closed. Configuration is fixed for each Manager lifetime; an older live task keeps its recorded launch budget.

Replace the existing “Pipelines use…” sentence with:

> Pipeline-generated stage/edge work keeps its existing internal routing and the same per-launch accounting; no separate pipeline scheduling policy is added.

### `05-Message_Flow_and_State.md` [MF-1], [MF-6]

Add this rule to [MF-1], and replace [MF-6]'s opening lane/observation description with it plus a reference to [MA-1.8]. Retain the existing seed/acknowledgement/recovery semantics and replace two-source enumerations with all three sources:

> Canonical Managers consume root requests from `weft.spawn.requests`, ordinary same-context task-child requests from `weft.spawn.children`, and existing internal requests from `weft.spawn.internal`, in internal, child, root priority order when eligible. Their respective reserved queues are `T{manager_tid}.reserved`, `T{manager_tid}.children_reserved`, and `T{manager_tid}.internal_reserved`. Each source/reserved pair uses the same exact-TID validation, inbox seeding, process launch, acknowledgement, and recovery path. A denied row stays unreserved in its source queue.
>
> The shared submission owner automatically chooses the child lane when the executing built-in host function/agent runtime supplies a same-context caller identity. Its spawn envelope contains `submitted_by: {tid, context}`; the Manager validates the full TID and canonical same-context identity. Caller-authored ancestry tags alone do not select the child lane. The actual submitter is retained in runtime evidence independently of `metadata.parent_tid`; absent an explicit ancestry tag, the tag defaults to the submitter for children and to the Manager for roots. Foreign-context submissions and submissions without runtime caller identity remain roots. Existing manager/internal and pipeline submissions preserve their explicit routing. Queued children remain accepted work if their parent later exits; this routing adds no cancellation or garbage-collection policy. Result readers and submission reconciliation recognize all three source/reserved pairs without resubmitting accepted work.

Add to `05-Message_Flow_and_State.md` [MF-5]'s current task-state contract and the matching `07-System_Invariants.md` [OBS.6] runtime snapshot description:

> Each new PostgreSQL launch carries the Manager-authored `admission` record with positive integer `connection_budget`, opaque string `connection_owner`, and nullable full-TID `submitter_tid`, including when admission is disabled. The record crosses wrapper and worker launch transport, appears in the existing Manager `task_spawned` event and every task-owned runtime snapshot, and is immutable for that task lifetime. Caller metadata cannot replace it. It does not add a TaskSpec `spec`/`io` field or an independently retained queue. Runtime snapshot retirement continues to use existing liveness ownership. Runtime submitter identity and child routing also operate with SQLite or a disabled gate, without importing PostgreSQL inspection or applying its connection-budget model to SQLite.

### `04-SimpleBroker_Integration.md` [SB-0.4]

Replace the optional admission helper paragraph with:

> PostgreSQL connection creation, owner labeling, and usage inspection belong to `simplebroker_pg`. Weft installs a stable task/context owner label before a dedicated runtime process opens any broker pool or listener, using the supported public binding capability. Both connection paths share that label without changing broker identity or multiplying session pools. Runtime labels confer no service authority. Weft consumes the public coherent total/by-owner usage snapshot through an existing Manager Queue; it neither issues backend SQL nor opens a measurement-only connection. Unknown attribution receives no discount. The existing four-integer `get_connection_stats()` interface remains available unchanged. [MA-1.8] owns prospective task budgets and admission decisions. The released public helpers are `simplebroker_pg.bind_connection_owner` and `simplebroker_pg.get_connection_usage`; require SimpleBroker >=8.6.0 and simplebroker-pg >=4.6.0. Owner labels match `[!-~]{1,54}`. Sample outside caller-owned transactions; absent owner counts provide zero credit, never death evidence.

### `07-System_Invariants.md` [MANAGER.16], [MANAGER.18]

Replace [MANAGER.16]'s first sentence with:

> Managers dispatch eligible internal work before ordinary task children, and eligible children before roots. All lanes remain subordinate to control/drain state and the existing bounded dispatch turn.

Replace [MANAGER.18]'s capacity/usage/no-accounting description through “production branch” with:

> Configured admission is a pre-reservation soft guard governed by [MA-1.8]. PostgreSQL accounted usage includes actual server pressure and each admitted task's not-yet-open connection budget without double-counting. Root admission cannot spend the ordinary-child headroom, and ordinary children cannot spend the internal reserve. Every candidate must fit its complete budget before launch. Startup state or elapsed grace cannot release a live obligation. SQLite retains its live-TID accounting and shared public capacity for roots and children. Admission reuses existing runtime evidence, liveness, and recovery owners; it adds no durable permit store or distributed quota service. Child routing does not imply unlimited nested-dependency progress.

Retain the remaining denial/control/diagnostic rules, changing “both spawn sources/lanes” to “all configured spawn sources/lanes”.

### `14-Python_API_Surfaces.md` [PY-1]

Insert:

> Ordinary `WeftClient` submission from a built-in host function/agent task uses that runtime's same-context caller identity for child routing. It does not require a Django adapter or a new submission method. Opening or retaining a client session does not change ancestry, admission authority, result semantics, or task execution limits. Submission outside such a runtime, including another context, remains root work.

### Supporting synchronized edits

Add the `weft.spawn.children` row to the queue inventory in `00-Quick_Reference.md`. Update source enumeration/reconciliation notes and [MA-1.8] implementation notes with code in the owning slices. Add this plan's backlink to each touched spec. Revise the August 26 “Admission Belongs Before Reservation” lesson's raw-observation-only advice to point to the new [MA-1.8], with a dated explanation that unmaterialized task connections were absent from that model. Preserve its valid pre-reservation and bounded-retry guidance. Do not rewrite historical plans.

## Implementation slices

Execute in order; separate reviews/read-only test runs may run in parallel, not overlapping edits to Manager.

1. **Upstream dependency and spec-promotion slice.** Use the evaluated released SimpleBroker >=8.6.0 and simplebroker-pg >=4.6.0 APIs recorded above; update all dependency declarations and the lockfile. Obtain independent review of this delta, then promote the exact Weft text above and record the promotion baseline. Files: the named specs, quick reference, plan index, lessons; later dependency floor in `pyproject.toml` and its lockfile. Stop if this requires connection leasing or a session rewrite. Done when the public prerequisite is available and the spec/plan checks pass. No Weft code cites unpromoted behavior.
2. **Runtime attribution and budget accounting.** Files: `weft/core/runtime_identity.py` (new private owner), `weft/core/launcher.py`, `weft/core/runners/host.py`, `weft/context.py`, `weft/core/task_state.py`, `weft/core/tasks/base.py`, `weft/liveness/policy.py`, `weft/core/manager.py`, `weft/core/admission.py`, `weft/_constants.py`; tests in `tests/core/test_admission.py`, `tests/core/test_manager.py`, and a new `tests/system/test_child_admission.py`. Install identity before broker opens, keep one label per full TID/context across wrapper/worker, transport the recorded budget, and calculate deficits through the public helper. Reuse current runtime reconstruction and incremental observations. First prove zero-open pending-launch oversubscription red; then prove open/close/reopen accounting. Stop if a guessed readiness timeout, per-admission world scan, or manager-authored replacement for task state appears. Done when real PG costs transfer without double-counting and failure/reap releases only the intended obligation.
3. **Child routing and protected capacity.** Files: `weft/core/spawn_requests.py`, `weft/commands/submission.py`, `weft/commands/_spawn_submission.py`, `weft/client/_client.py`, `weft/core/manager.py`, `weft/core/admission.py`, `weft/_constants.py`; audit other source/reserved consumers by searching the two old queue names. Tests: `tests/core/test_spawn_requests.py`, `tests/commands/test_submission.py`, `tests/core/test_manager.py`, and `tests/system/test_child_admission.py`. Reuse the shared writer and source/reserved pairs. Cover native/fallback watcher interest, idle detection, drain episode reset, leadership, restored rows, and exact-TID submission reconciliation for the new pair. A retained submission Queue is a connection facade, not authority to force the old root route. Do not edit result consumption or revive the unused `delete_spawn_request` rollback path. No dependency graph or special LLM code. First prove saturated roots plus a child behind root backlog fail red, then complete through the real child lane. Stop if internal service authority is inferred from application metadata or pipeline bootstrap is being redesigned.
4. **Integrated proof and traceability.** Run the tests/gates below, perform independent implementation review, reconcile spec/code backlinks, and record evidence. Use existing admission operational logging for `observed`, `prospective`, `accounted`, candidate budget, lane/ceiling, and reason. Do not create a metrics service or enable logging globally without operator approval. Only after the normal release/CI/deploy path is separately authorized, assess a natural burst by useful completions, child start lag, role rejections, parent timeouts, and eventual queue cleanup. A quiet interval is not burst proof.

## Tests and verification

Keep queues, Manager, processes, session lifecycle, reservations, and results real. Use `WeftTestHarness`, the PG fixture/wrapper, and deterministic queue/barrier coordination. Fake only the external LLM call with a local function returning a known value. Pure arithmetic tests may pass literal observations to `AdmissionController`; do not substitute them for the PG integration proof. No fixed sleeps as correctness assertions, no mass monkeypatching, no tests pinning this plan's process wording.

| Proof | Observable assertion |
| --- | --- |
| Pending/open/closed/reopened connections | Delayed real workers cannot exceed their accounted budgets; opening attributed pool/listener connections transfers cost; closing and reopening does not create free admission capacity. Also test unknown labels and actual usage above the selected budget. |
| Saturated roots with leaf children | Fill the root budget with real parents waiting on local leaf tasks, leave root backlog ahead, and verify every parent receives its result before its timeout. Observe no PG role rejection; use enough budget for actual service overhead, not synthetic limits smaller than the runtime itself. |
| Ownership and route | Host function and agent workers route same-context submissions as children, with actual submitter TID; external client, foreign context, and ancestry-tag-only submissions remain roots. Cover retained and bounded clients, explicit ancestry tags, and malformed/mismatched child envelopes. |
| Failure/recovery | Failed launch without a process, real exited process tree, delayed child mapping, and Manager replacement with retained reserved/launch evidence release or retain exactly the correct TID. Preserve accepted queued children after parent exit. |
| Nearest boundaries | Exact-fit candidate admits; one-connection-short denies; unavailable observation denies without a spin; STOP/internal work still progress; existing pipeline routing and SQLite execution still work. New config changes observable capacity and invalid values fail at load. |
| Scope limit | Document nested wait limitations in the user-facing admission text. Do not add a flaky timeout test or build a cycle detector merely to demonstrate an explicitly unsupported progress guarantee. |

Commands from the Weft root, always after `set -a; . ./.envrc; set +a`:

```bash
# Plan-authoring checks; rerun after review edits.
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q -n 0
git diff --check

# Implementation: narrow checks, then actual PostgreSQL proof.
./.venv/bin/python -m pytest tests/core/test_admission.py tests/core/test_spawn_requests.py tests/commands/test_submission.py -q
./.venv/bin/python -m pytest tests/core/test_manager.py -k 'admission or child_launch or reserved or leadership' -q
./bin/pytest-pg tests/system/test_child_admission.py tests/system/test_admission_budget_pg.py tests/core/test_manager.py

# Final implementation gates, not required merely to author this document.
./.venv/bin/ruff check .
./.venv/bin/ruff format --check .
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/python -m pytest -m ""
./bin/pytest-pg --all
../backstitch/.venv/bin/backstitch check --repo-root . --spec-root docs/specifications --plan-root docs/plans --code-root weft --code-root tests --test-root tests --format json
```

Record any existing traceability failures against a rerun of the named baseline; do not silently waive a required gate or call it green. Upstream tests and normal release gates remain separate requirements. A plan-authoring pass does not prove the new runtime works.

## Out of scope

No timeout increases, PostgreSQL limit increases, mm-governance fanout rewrites, inline execution, changing result consumption, queue cleanup policy, arbitrary dependency graphs, work stealing, resource suspension, automatic parent cancellation, new monitoring framework, production mutation, or release/deploy in this planning turn. No blanket hard guarantee for custom process trees or external connection users. Do not merge unrelated historical plan work into this fix.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

No implementation has been performed under this plan.

## Review and execution record

Required review: a separate author fresh-eyes pass, then an independent reviewer of the plan, exact delta, and source files. Review questions: could a zero-context engineer implement this correctly; would it impair robustness; does any mechanism or test add ceremony without protecting the observed failure? Prefer removing unnecessary work. Verdict is PASS or BLOCKED with concrete findings and explicit author dispositions. Repeat review for substantive accepted fixes and after implementation slices.

Reviewer availability, October 13: Claude's configured OAuth session could not authenticate; Gemini's configured runner refused an untrusted directory. Neither credentials nor workspace trust were changed. Independent same-family review is available; different-family review is not claimed.

Authoring baseline: six plan metadata/spec hygiene tests passed before edits. Fresh-eyes and independent review results, post-edit gates, and any unresolved limitations are recorded below before handoff.

### October 13 author fresh-eyes and independent review

The author re-read the full draft separately from drafting and corrected these gaps before handoff:

| Finding | Disposition |
| --- | --- |
| Restarting only the Manager leaves old workers without ownership labels or recorded budgets. | Require draining old-version active/in-flight work before upgrade. Preserve unadmitted root backlog; add no compatibility estimator or deploy guard. |
| Caller ancestry and runtime connection evidence needed concrete custody and schema. | Specify the immutable launch/snapshot admission record and separate runtime submitter envelope; preserve explicit application ancestry. Record PG budgets even with admission disabled. |
| A new queue affects accepted-submission discovery, not just Manager dispatch. | Name the live `_spawn_submission.py` consumers. Exclude the unused legacy deletion helper and unrelated result-consumption changes. |

Independent read-only review returned **PASS**: implementable as written subject to the explicit SimpleBroker interface/release prerequisite; no remaining design blocker within the stated leaf-child workload and configured budgets. The reviewer independently identified the old-worker rollout gap (resolved), requested naming the submission-reconciliation owner (accepted above), and cautioned that terminal-without-positive-process-proof in `mapping_row_is_live()` is insufficient to retire a PG obligation. Accepted: implementation must use scoped process-tree/recovery evidence, including worker identities already present in task snapshots, rather than treating wrapper reap or terminal state as whole-tree death.

The reviewer checked correctness, implementability, robustness, and unnecessary complexity against the cited source baseline and verified the revised rollout and evidence contract. No permit service, dependency graph, wait-registration protocol, special result reader, or coded deployment boundary was requested or added. This is a same-family independent review, not a successful different-family check.

The plan is ready for design approval. Weft implementation remains gated on the narrow upstream interface being approved, published, and pinned. Normative spec text has not been promoted; related-plan backlinks are non-normative. No implementation, production action, or commit was performed in this authoring turn.

Post-review authoring validation: `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q -n 0` passed all six tests; `git diff --check` passed. Runtime, PostgreSQL integration, lint/type, and final implementation gates were not run for this documentation-only change and are not claimed green.

### October 13 implementation authorization and released prerequisite

Class 5 with hardening retained. Owner authorized updating the floors and implementing this plan. Preserve the existing uncommitted workflow uv pins and documentation. Independent review of the release interface/delta precedes promotion; runtime, routing, accounting and integrated proof remain the implementation slices. No push, release or deployment is authorized.

### Spec promotion and pre-implementation controls

Promotion baseline: diff base `5f588e9ddc7c6d1f2141d9c645725a70382006ef` plus the promoted requirement edits in `docs/specifications/03-Manager_Architecture.md`, `04-SimpleBroker_Integration.md`, `05-Message_Flow_and_State.md`, `07-System_Invariants.md`, `14-Python_API_Surfaces.md`, and `00-Quick_Reference.md`. Independent revised-plan/interface review returned PASS before promotion. Dependency resolution upgraded only SimpleBroker 8.5.1 to 8.6.0 and simplebroker-pg 4.5.1 to 4.6.0. Metadata/spec hygiene gates passed six tests after promotion.

Red controls before production edits: seven failing whole-candidate-budget/child-lane boundary cases; six failing real queued/reserved submission-routing and reconciliation cases; one real wrapper/host-worker task returned no runtime identity. These failures expose missing behavior rather than setup/import failures. The test-audit authoring gate applies to all new coverage.

Implementation clarification: runtime recovery evidence reads can fail with `BrokerError`, `OSError`, `RuntimeError`, or `ValueError`; those unknown observations fail closed. The PostgreSQL helper boundary retains its narrower `DatabaseError`/`ValueError` handling. The promoted exception paragraphs are synchronized with these separate scopes.

### Slice review findings and dispositions

Independent runtime/routing reviewers found three unsafe retirement paths in the first implementation. Accepted and repaired: use the existing tri-state `inspect_host_process()` and preserve hostname (unknown/foreign scope retains debt); PG task-owned snapshots retain wrapper and worker identities separately from the existing runtime handle; a successful launch followed by cleanup failure retains its Process identity and obligation. A fourth finding exposed disabled-gate accumulation; actual reap now refreshes only that task and retires positively dead evidence even with admission disabled. Refresh failure retains debt while the existing child reap/control cleanup still finishes. Malformed runtime record/host identity data fails closed at the recovery boundary. No change to SQLite liveness or runtime-handle semantics.

Additional source reconciliation includes ordinary child backlog in public Manager-availability recovery and preserves child source/reserved fields in service-registry projection. The standalone backlog reader retains its Queue facade lifetime; it does not introduce a nested BrokerSession close. Child reservations remain under the existing Manager reserved policy/recovery and are not added to internal-only immediate deletion.

Final recovery review found that scanning every task's `.reserved` queue could resurrect a retired launch obligation from ordinary retained consumer input. Reproduced with and without caller-supplied `role=manager`. Bootstrap now discovers Manager custody from structural Manager snapshots, canonical service-owner records, and retained `task_spawned` reporter TIDs, then consults only those Managers' three reserved queues. Historical stopped owners remain eligible; unreserved source backlog never creates an obligation. Child-only launch evidence is projected from `task_spawned`: the reporting Manager's runtime handle is not child process evidence. A live reporter plus positively dead child tree reproduced this second defect before repair. Bootstrap does not retain historical TaskSpecs in memory.

Malformed child JSON objects, origin envelopes, and forged origin on root/internal sources receive typed per-task rejection and the existing reserved disposition. JSON array/null probes reproduced stranded reservations before the non-object guard. An actual submitter process durably accepts a child and exits before the Manager executes it, verifying that accepted children do not require a live parent. Admission data remains in launch/runtime evidence; it adds no PING/STATUS admission schema fields.

Normal PostgreSQL retirement requires task-owned finalization and exact whole-tree death. An abnormal exit before final evidence can leave an obligation unresolved indefinitely. This is the conservative recovery boundary for unpublished worker starts, not an expiry rule or proof from absent owner counts. The README and promoted specifications describe this operational limit.

### Regression sensitivity and verification

Three real-PG control mutations reproduced their intended failures: replacing prospective accounting with raw total lost the zero-open task budget; forcing child submissions to the root source blocked actual leaf readiness after roots saturated; bypassing reap-time evidence retirement left completed tasks' published obligations behind with the gate disabled. Each mutation was restored byte-for-byte. The restored three-test PG integration selection passed together under the canonical wrapper and normal xdist settings (24.67 seconds).

Seven focused recovery mutations also fired the eleven existing recovery cases for unknown/foreign evidence, live worker behind a dead wrapper, disabled retirement, corrupt evidence, pending-launch budget reconstruction, pre-process failure release, and post-launch cleanup custody. Subsequent consumer-reservation, reporter-handle, and malformed-envelope regressions separately failed on the corresponding pre-fix implementation. These are executed behavioral controls, not assertions about source text.

The former single-connection scheduler/diagnostic tests explicitly select a one-connection budget where their small fixtures require it. Their scheduler purpose is retained; the new default-eight exact-fit/one-short boundaries and real PG saturation proof govern the changed budget contract. Work remains uncommitted for owner review; no release or deployment has occurred.

### Final local verification, October 13

The final implementation review returned PASS within the approved built-in host leaf-child scope. The author separately reviewed the final recovery, dispatch, and transport boundaries. A later wrapper checkpoint now unions previously observed workers and preserves a known exact wrapper identity when a fresh lookup is unavailable. Its regression failed before repair by losing the live worker from durable Manager evidence. No unresolved implementation blocker remains in this slice.

| Gate | Final result |
| --- | --- |
| Full SQLite, `./.venv/bin/python -m pytest -m ""` | 5,615 passed, 48 skipped, 365.22 seconds; normal 12-worker settings |
| Full PostgreSQL, `./bin/pytest-pg --all` | 5,566 passed, 26 skipped, 350.43 seconds; PG18 and normal 12-worker settings |
| PostgreSQL Manager plus admission integration selection | 275 passed, 11 skipped, 36.71 seconds |
| Ruff lint and format | Passed; 800 files already formatted |
| Full configured mypy scope | Passed; 460 source files |
| Documentation metadata/spec hygiene and diff checks | Passed; six documentation tests and diff checks |
| Backstitch traceability | Global gate remains failing on existing debt: 34 errors and 1,115 warnings. Exact baseline `5f588e9ddc7c6d1f2141d9c645725a70382006ef` reproduced 34 errors and 1,142 warnings. Comparing stable issue identities without line numbers found zero new errors or warnings; 27 previous warnings were resolved through touched mappings. This is not a global-green claim. |

Initial full-suite failures were diagnosed and corrected before these final passes. SQLite found the four new core modules missing from the mandatory shared-backend classification table; their local shared markers already exercised them, and explicit table entries now satisfy the repository policy. PostgreSQL found an older two-slot leadership fixture expecting a one-connection candidate while inheriting the new default-eight budget. Selecting its intended budget of one preserves the leadership assertion and the new whole-candidate admission contract. The default-eight tests and production policy were not weakened. Both full suites were then rerun successfully.

The plan remains `draft` because the working changes have not landed. The touched implementation is in `weft/_constants.py`, `weft/context.py`, `weft/core/admission.py`, `weft/core/runtime_identity.py`, `weft/core/launcher.py`, `weft/core/runners/host.py`, `weft/core/tasks/base.py`, `weft/core/manager.py`, `weft/core/spawn_requests.py`, `weft/core/manager_runtime.py`, `weft/core/service_convergence.py`, and `weft/commands/_spawn_submission.py`. Dependency declarations and the lockfile are updated; the earlier requested workflow uv pins remain at 0.13.0. The six promoted specs, README, plan index, lessons, test classification, and associated core/command/system tests are synchronized. There is no commit, push, release, deployed-runtime mutation, or assessment of a live production burst in this handoff.

## October 13 revision: shared crash judgment and bounded refresh

Class 5, hardening required: changes admission retirement authority and reactor
scheduling. User explicitly requests recovery after crashes using
LivenessMonitor's judgment, and no full task reread per admission decision.
Baseline: `5f588e9ddc7c6d1f2141d9c645725a70382006ef` plus the preceding admission
implementation and promoted spec delta described above. Preserve that work and
unrelated workflow pins. This is a two-defect revision, not a scheduler redesign.

Outcomes:

- [x] Crashed tasks release budgets when the shared liveness API reports stale,
  without needing the dead task to publish a terminal snapshot.
- [x] Live workers, unresolved identities, foreign-host evidence, and locally
  pending launches remain charged.
- [x] After bootstrap, admission decisions perform no task-state reads; bounded
  reactor maintenance and exact child-reap observations keep evidence current.
- [x] Missing task-state queues retain previously known process evidence;
  corruption/read errors retain budgets and fail admission closed until a
  successful reconciliation.

Read/change owners: `weft/core/manager.py` owns pending obligations, bootstrap,
reap, and reactor deadlines; `weft/liveness/analysis.py::analyze_liveness` is the
broker-free API already used by `weft/core/tasks/liveness_monitor.py`; reuse it,
do not change its judgments or add another liveness service. Existing retained
host-process identities are adapted to a host-authority `RunnerHandle` for this
API. That represents the budgeted local wrapper/worker processes, not a claim
of authority over an external runner. Pass only that host handle, without the
task's terminal hint: admission asks whether the processes are stale, so a
terminal hint must not turn an unknown process probe into released credit.
Keep the existing foreign-host guard.
`weft/core/task_state.py` remains the state reader. Change only the admission
call sites, not all readers. Comprehension checks: why must a pending launch
remain charged before its PID exists; why can stale cached evidence only delay
capacity release, rather than create additional launch credit?

### Exact proposed delta and promotion

Strategy A, requirement text before code. In `03-Manager_Architecture.md`
[MA-1.8], replace the whole-tree-proof and abnormal-exit paragraphs with:

> Manager retires a PostgreSQL connection obligation when the shared
> `analyze_liveness` API used by LivenessMonitor reports stale for the retained
> local wrapper/worker identities. A task-owned terminal snapshot is not
> required, and its terminal hint is not supplied to the process probe. Live
> or unknown evidence, foreign-host scope, and locally in-flight
> launches retain their budgets. Missing owner labels, missing task-state rows,
> and elapsed time alone grant no credit. This accepts the existing sampled
> liveness judgment rather than requiring proof of unpublished descendants.
>
> Bootstrap reconstructs admitted obligations once. New launches are charged
> immediately and reaped children receive an exact-task refresh. Steady-state
> admission uses cached obligations plus a fresh connection-usage snapshot,
> without rereading task-state queues. On the existing one-second admission
> recheck cadence, reactor maintenance refreshes at most 16 obligations in
> round-robin order, including with the gate disabled and while idle. The next
> wait deadline includes this maintenance. Retained stale evidence may delay
> credit until its batch is checked. Read or validation failures retain debt
> and fail admission closed until reconciliation succeeds. No new config,
> background service, per-task watcher, expiry policy, or durable verdict is
> introduced.

Keep the existing accounting formula, launch evidence and reconstruction
rules. Synchronize [MANAGER.18], README admission text, and the admission lesson.
In `07-System_Invariants.md` [LIVENESS.R2], replace the blanket ban on admission
use with:

> LivenessMonitor does not synthesize TaskSpec transitions, task-log verdicts,
> Manager ownership, process signals, or deletions outside task-state rows.
> Manager may reuse its broker-free liveness analysis to retire connection
> obligations under [MA-1.8]; this grants no task-state deletion authority and
> does not reuse the monitor's age or unknown-timeout deletion policy.

### Slices, constraints, and proof

1. Review this delta independently and with author fresh eyes, then promote
   it. No new dependency/API/queue/result schema or changes to SimpleBroker,
   LivenessMonitor cleanup, SQLite admission, budgets, or child priority.
2. Extend real-process recovery tests in `tests/core/test_admission_recovery.py`
   to show nonterminal crashed processes recover capacity while live/unknown
   workers do not (including terminal hints with unresolved process identity).
   Prove the current terminal gate fails first. Replace that
   gate with the shared liveness judgment. Retain absent-state evidence.
3. Add a deterministic read-count regression at the actual Manager boundary
   using Python call profiling (observation only, no broker substitution):
   repeated admission decisions must not read task snapshots. Prove it fails
   first. Add a named batch constant in `weft/_constants.py`, one maintenance
   deadline, round-robin bounded refresh, and existing reactor/wait integration.
   Failed batches keep their failed item due for retry; no rotating a broken
   record out of view and declaring observation healthy. Bootstrap remains
   one-time; no per-decision history replay. Test idle/disabled-gate scheduling,
   batch bounds/fairness, recovery after read errors, and exact reap refresh.
4. Rerun the real-PG parent/child proofs plus crash recovery, neighboring
   Manager/liveness tests, Ruff, configured mypy, full SQLite and PG suites,
   six spec/metadata checks, and the backstitch command above. Compare existing
   traceability debt, do not claim global green if it remains. Independent
   final review checks only this revision and introduced regressions.

No production mutation or release/deploy. Existing coordinated upgrade guidance
still applies. This revision adds no persisted format: rolling back just these
fixes restores the known capacity leak and per-decision scan, not a data repair.
Keep real brokers/processes; do not mock admission or liveness verdicts.
Stop if implementing this needs a second liveness owner or monitoring framework.

Fresh-eyes finding: [LIVENESS.R2] explicitly prohibited admission use even
though its analysis is reusable. The exact delta above makes the requested
authority change explicit. Unknown evidence remains conservative; we do not
copy the monitor's unknown-expiry policy. Independent same-family review
returned PASS. Accepted clarification: omit the terminal hint from process
analysis, so unresolved identities remain charged. Failed maintenance items
stay first for retry; only a complete successful batch/bootstrap clears the
unavailable flag, never an unrelated successful reap. This needs no new store.
Different-family authentication/trust blockers noted above remain unchanged.

Promotion baseline: diff base `5f588e9ddc7c6d1f2141d9c645725a70382006ef` plus
the preceding implementation and this revision to [MA-1.8], [MANAGER.18], and
[LIVENESS.R2] in the governing spec files. Requirement text was promoted before
the recovery/maintenance code. Existing backlinks retain this plan as owner.

### Revision implementation and verification, October 13

Implemented in the existing Manager owner: shared `analyze_liveness` on retained
same-host identities (without terminal hints), one-time bootstrap, cached
admission decisions, exact reap refresh, and a 16-item round-robin maintenance
batch on the existing one-second cadence. No SimpleBroker or LivenessMonitor
behavior changes, new service, verdict store, public configuration, or expiry
rule were needed. Unknown/foreign/pending identities remain charged. This is
sampled process judgment, not proof about unpublished descendants.

Independent review caught reap-first read failures that did not close admission.
The regression reproduced that failure before repair. Reap failures now mark
evidence unavailable and prioritize that record. While unavailable, subsequent
reaps retain debt and defer to bounded maintenance. This avoids a failed-TID
store and prevents a burst of failed reaps from displacing the first failed
record. A successful complete batch clears the flag. The final independent
review returned PASS with no remaining scoped findings; author fresh-eyes review
also checked the cache, liveness adapter, and deadline paths. Earlier
different-family authentication/trust limits remain recorded above.

The diagnosing-bugs and test-audit checks drove executed regression controls:
nonterminal dead processes retained eleven units of budget before repair; five
admission decisions reread five task snapshots before caching. Reap-first
corruption incorrectly allowed accounted usage of 36 before fail-closed repair.
Both corruption entry paths now cover eighteen subsequent reaps with no extra
reads or lost failed-head priority. Removing the maintenance deadline made both
enabled/disabled deadline cases fail (`[]` instead of `[0.0]`); the production
line was restored. That control also exposed and replaced an initially weak
assertion masked by unrelated immediate timers. These tests observe the real
state reader through call profiling; no new product test seam was introduced.
Real-PG tests kill the isolated task's wrapper and worker, observe budget
retirement while its snapshot remains nonterminal, and run follow-up work.

Final commands source the repo environment. Full-suite commands additionally
use `env -u DJANGO_SETTINGS_MODULE` because the calling mm-governance environment
otherwise points Django at `ops_web`. The initial SQLite run had four fixture
collection failures for that missing module, not runtime failures. Clearing only
that inherited selector made all six fixture tests pass; no product/test change
was needed for them. The first full PG run exposed two timer fixtures that had
not completed the newly scheduled bootstrap. Their steady-state setup now calls
the real refresh owner; the expected timer values were not weakened. The PG
deadline/recovery selection then passed 34 tests, and both full suites were rerun
after the final test changes using normal parallelism and limits.

| Gate | Revision result |
| --- | --- |
| Full SQLite: `env -u DJANGO_SETTINGS_MODULE ./.venv/bin/python -m pytest -m ""` | 5,626 passed, 50 skipped, 319.63 seconds |
| Full PostgreSQL: `env -u DJANGO_SETTINGS_MODULE ./bin/pytest-pg --all` | 5,579 passed, 26 skipped, 627.27 seconds |
| Real-PG admission/recovery/child selection before final timer-test refinement | 37 passed, 22.83 seconds |
| Ruff check and format | Passed; 800 files formatted |
| Full configured mypy command above | Passed; 460 source files |
| Backstitch command above | Still fails on existing 34 errors and 1,115 warnings; stable issue comparison excluding line numbers against the pre-revision 34/1,115 report found no added or removed errors/warnings |

Full-run logs: `/tmp/weft-admission-sqlite-final-20261013.log` and
`/tmp/weft-admission-pg-final-20261013.log`. The plan/spec metadata tests and
`git diff --check` are rerun after this record is added. The traceability gate
is not globally green, and local tests are not evidence of live burst efficacy.
Changes remain uncommitted alongside the preserved preexisting implementation;
no commit, release, deployment, or production mutation was performed.
