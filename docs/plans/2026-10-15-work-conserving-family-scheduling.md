# Work-Conserving Family Scheduling

Status: completed
Source specs: docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/02-TaskSpec.md [TS-1]; docs/specifications/12-Pipeline_Composition_and_UX.md [PL-0.2]; docs/specifications/03-Manager_Architecture.md [MA-1.8]; docs/specifications/07-System_Invariants.md [MANAGER.18], [OBS.6]; docs/specifications/05-Message_Flow_and_State.md [MF-5], [MF-6]; docs/specifications/00-Quick_Reference.md configuration table
Superseded by: none

Class: 5. Intended scheduling and configuration behavior changes; execution-path hardening is mandatory. Escalated from a Class 0 diagnosis after the owner explicitly required removal of the cap on 2026-10-15.
Plan type: implementation with spec revision.

## Goal and acceptance

Remove the five-frontier cap entirely. The existing measured formula (`available = max(0, ceiling - reserve - used)`) decides ordinary capacity; existing family ordering chooses the next eligible request. Keep the current single launch worker, fresh startup feedback, broker accounting, reservation/checkpoint custody and event reactor. Bounded classification pages are responsiveness limits, never live-family limits.

- [x] More than five finite branches can launch while measured admission permits, including independent roots and one root's sibling fanout.
- [x] Available tip children outrank fresh independent work; FIFO family entry, round-robin paths and LIFO tip descent remain deterministic.
- [x] Measured saturation still prevents process creation; real labeled/unlabeled pressure counts, release permits progress, and the bounded-input finite rollup/enrichment/LLM proof completes.
- [x] Unknown launch evidence and failed checkpoint writes still gate ordinary dispatch independently of scheduling policy.
- [x] Removed frontier configuration fails with an actionable migration error; no hidden constant, huge sentinel or replacement cap remains.
- [x] Current specs, theory, README, tests and nearby implementation notes describe the same contract.

## Source documents and baseline

Baseline: `601ecb207e657882bb6fb1d834e66596bb13e2e9`. Required conceptual reading: `docs/program-theory.md` [WT-2], [WT-4] section 4.2, [ALT-WT-001], [WT-7], [REV-WT-003]. Its fixed-frontier account is explicitly reconsidered by the owner's current instruction, not silently retained as intent. This revision supersedes only the concurrency-cap and cap-derived progress claims of `2026-10-14-measured-admission-family-scheduling.md`; measured accounting, lineage and custody remain intended.

Consulted: canonical hub, decision hierarchy, principles, engineering principles, runtime/context runbook, writing/hardening plans, review-loop runbook, lessons pointer and Golden Rules/recent lessons. Relevant execution owners are `weft/core/family_scheduling.py`, Manager `_peek_family_candidate`, `_admission_allows_queue` and existing startup/custody paths, `weft/core/admission.py`, and `_constants.py` configuration schema. No module theory file exists beside these modules at the baseline.

Classical model: work-conserving scheduling with measured backpressure; child-first fork/join ordering with work sharing. This is an adaptation, not a claim of Cilk's execution model or general deadlock freedom. CPUs release their execution slot when a task blocks; Weft's waiting processes can retain connections. The owner explicitly rejects a family cap. Do not restore one to recover the old `P * H` proof.

## Evidence and difference register

The ops monitoring thread `Investigate broken Weft ops (2)` reports deployed default `SCHEDULER_MAX_FRONTIERS=5` with ample database capacity. A direct current-code probe creates six independent pending roots, launches five, and evaluates `AdmissionController(300, .1, True)` at used=33. Admission permits ordinary work with 237 available; `FamilyScheduler.select()` returns None. The assertion `scheduler.select() is not None` fails. The causal checks are `family_scheduling.py`'s `select` early return and `launched` spare-frontier exception. This is a design-policy defect, not failed connection accounting.

| Boundary | Current behavior | Required treatment |
|---|---|---|
| Frontier count | Positive configured default 5; rejects sixth unrelated path | Retire both runtime checks and the setting; no replacement cap |
| PostgreSQL default ceiling | Zero currently bypasses resource admission | Zero/unset derives backend C via public API; positive override remains |
| Ordering | Tip-child RR/LIFO, independent FIFO, then older sibling sharing | Preserve ordering, allow another path without a spare permit |
| Startup feedback | One launch observed before the next measured sample | Preserve notification-only gate and absence deadline |
| Custody | Unresolved/undurable launch may block fresh work | Preserve every guard and exact ACK rule |
| Background | Startup path then measured background resident | Preserve lineage/class handling; no scarce frontier permit |
| Work per turn | Bounded source pages; in-memory O(W) selection | Preserve bounded broker work; characterize cost against live paths |
| Progress | Fixed W and `E+I+B+P*H` sufficient envelope | Replace with honest measured-headroom conditions; no general deadlock claim |
| Tests | W1/W5 often doubles as a synchronization device | Use real phase events/custody or explicit resource conditions, never a hidden replacement cap |

Comprehension answers before implementation: (1) a pending row occupies no process slot; a waiting live parent still contributes measured usage; (2) a positively published snapshot can release startup gating but cannot ACK without matching task-owned custody evidence or positive settlement; (3) child priority only acts on discovered requests, so it cannot conjure resources for unpublished descendants. These facts determine the tests below.

## Invariants, ownership and constraints

Keep [IMPL.8-11], fixed delivery/dynamic notification topology [QUEUE.7-8], scoped positive exit evidence, immutable runtime identity, and LivenessMonitor-only task-state deletion. Keep single `weft.spawn.requests`, exact-ID reservation and consume-on-dispatch label queues. Do not add a child lane, per-task charge, lookahead reservation, polling timer, readiness helper, process limit, resource prediction, new wait protocol or dependency declaration. Internal service/pipeline admission reserve rules and external/unattributed accounting stay unchanged. No production tuning/deployment is authorized by this plan. The PostgreSQL default-policy change was separately authorized by the owner on 2026-10-15 and is part of the exact amendment below.

The task is isolated in a managed worktree. Preserve the primary checkout's unrelated supervisor/monitor changes. No deletion of historical plans or tests solely to obtain green. Preserve original deadlines, physical PostgreSQL limits and 12 pytest workers. Any urge to reduce contention, serialize or extend waits is a stop condition requiring a causal diagnosis.

## Proposed Spec Delta

Promotion strategy A: promote exact requirement text in existing specs before runtime changes; existing mappings continue to name the same owners, and their changed behavior is verified in the implementation slice. No new section IDs or persistence schema.

In both [MA-1.8] and [MANAGER.18], replace the sentence `The disabled resource gate does not disable scheduling classification or its frontier bound.` with `On SQLite, a disabled resource gate does not disable family classification or launch custody.` Replace the entire paragraphs beginning `Ordinary launch selection is bounded depth-first` and `Progress is conditional on finite accepted work` with the exact selection/progress text below. Preserve startup-feedback, ambiguity, drain-wait, positive-exit, and cleanup clauses outside these spans.

### Selection replacement

> Ordinary launch selection is work-conserving family scheduling over dynamically observed ancestry. There is no configured frontier count or family concurrency cap. Active branches receive round-robin turns and offer newest eligible tip children first. If none has an eligible tip child, the oldest independent pending family enters FIFO by original root ID; otherwise an active family may offer its oldest unassigned sibling subtree, choosing families round-robin. A live finite tip and its ancestors retain lineage, not a capacity permit. A child extends its matching ancestry path; a sibling starts another path when selected and when measured admission and existing launch custody allow it. Positive process exit contracts a path without dropping known descendants. Logical family membership remains while pending or resident descendants exist; late-discovered work rejoins at the original root FIFO position or extends a matching live tip. Queued requests incur no process or prospective connection charge. Multiple Managers retain exact-ID work stealing and measure the same applicable cross-process resource population.

### Progress replacement

> Family ordering and resource admission answer different questions. The scheduler must offer eligible indexed work whenever it exists; only measured admission and the existing startup, ownership, and recovery-custody gates may defer its launch. This uses available resource capacity without a fixed family limit. Finite independent-subtree workloads can drain when leaves terminate, required descendants are eventually submitted, broker maintenance is fair, and measured headroom eventually permits each required launch. Depth-first preference prevents already-known descendants from sitting behind independent parent backlogs. It does not guarantee progress if already-running parents retain all capacity needed by descendants submitted later. Resource limits, external saturation, unbounded work, ambiguous launch custody, and arbitrary cross-task waits remain real limits. There is no runtime frontier allowance, prospective path reservation, or child-count declaration in this condition.

### Background replacement

In [MA-1.8], replace only the paragraph beginning `Validated persistent or interactive tasks use the ordinary primary source and measured admission.` with:

> Validated persistent or interactive tasks use the ordinary primary source and measured admission. Their lineage remains in the startup path until positive startup evidence or the bounded missing-snapshot fallback clears the existing startup gate; afterward they become background residents while their actual usage remains measured. Their validated scheduling class and lineage remain in the retained Manager checkpoint until task-owned snapshot handoff or positive settlement. Releasing startup lineage does not acknowledge the reservation. Background ancestry is transparent when locating a finite child scheduling parent. A child with no active finite ancestor starts a new ancestry path subject to ordinary measured admission and custody, without a spare-frontier requirement. Activity snapshots or timeouts cannot infer this lifecycle class. Unmarked long-running one-shot tasks retain their finite ancestry. Background and finite resource use both participate in measured usage.

### Classification replacement

In [MA-1.8], replace only the paragraph beginning `Classification advances in bounded pages even while admission is denied or all frontiers are occupied.` with:

> Classification advances in bounded pages even while admission is denied. A periodic resumable source repair covers explicit older request IDs and returned rows below the classification watermark. Completion and frontier reuse consume known indexed work directly; their broker-operation count must not grow with unrelated backlog. Cache contracted scheduling-parent pointers; do not derive a full graph per dispatch. Startup reconstructs living paths from task snapshots and surviving Manager checkpoints, then repairs source indexes. In-memory selection and path bookkeeping scale with live ancestry, not a fixed number of frontier places; no replacement concurrency cap or fixed FIFO visibility window is introduced.

### Configuration replacement

In [MA-1.8], replace only the paragraph beginning `Reserve fraction is finite in `[0,1)` and defaults to `0.` with:

> Reserve fraction is finite in `[0,1)` and defaults to `0.1`; reserve `R` is `max(ceil(C*f), 3 + int(liveness_monitor_enabled))`. Internal runtime work may use the reserve but still requires measured usage below C. Unset or zero `WEFT_ADMISSION_MAX_CONNECTIONS` selects the backend maximum on PostgreSQL and disables the resource gate only on SQLite. A positive configured ceiling overrides automatic capacity. The removed `WEFT_ADMISSION_TASK_CONNECTION_BUDGET` and `WEFT_SCHEDULER_MAX_FRONTIERS` settings raise actionable configuration errors directing operators to measured admission and its global reserve.

### Other affected exact clauses

- Apply the Background replacement above verbatim to `02-TaskSpec.md` [TS-1]'s paragraph beginning `Validated persistent or interactive tasks use`.
- In [MF-5] (`05-Message_Flow_and_State.md`) and [OBS.6b] (`07-System_Invariants.md`), replace only `candidate selection scans only the bounded W frontiers, without backlog-sized broker reads at completion` with `candidate selection is in-memory and proportional to live paths, without backlog-sized broker reads at completion`. Keep every token/schema/consume/repair/ancestry clause. Keep the wire key `scheduler_frontiers` unchanged.
- In [MANAGER.16], replace only `ordinary bounded FIFO/LIFO frontier selection` with `ordinary FIFO/LIFO family selection`.
- In `12-Pipeline_Composition_and_UX.md` [PL-0.2]'s paragraph beginning `Pipeline stage and edge bootstrap`, replace `nearest ordinary frontier ancestor` with `nearest finite scheduling ancestor` and replace `Ordinary descendants must remain eligible when all ordinary frontier places are occupied by their pipelines.` with `Ordinary descendants remain eligible subject to measured admission and custody; pipeline ancestry introduces no family-cap requirement.` Keep internal route, family inheritance, measured use and metadata-rejection clauses.

### PostgreSQL backend ceiling by default

Owner decision, 2026-10-15: in response to the default-policy question, "Use MAX_CONNECTIONS - RESERVE" and "That is what the value is for". PostgreSQL must not default to admission-off.

In [MA-1.8] and [MANAGER.18], add this exact rule after the initial admission paragraph:

> On PostgreSQL, unset or zero `WEFT_ADMISSION_MAX_CONNECTIONS` means automatic backend capacity, not disabled admission. On each fresh admission observation the Manager reads `max_connections` through SimpleBroker's public `get_connection_stats()` on its existing Queue, then reads the coherent total/by-owner usage through public `get_connection_usage()` outside an encompassing caller transaction. The ordinary gate is `available = max(0, C - R - U)` with that backend maximum C and the existing global reserve R. A positive configured ceiling overrides automatic capacity. Failure to obtain either capacity or usage defers launches as an unknown observation and preserves accepted work, custody and existing drain-wait clocks. Updating the effective ceiling does not reset those clocks. There is no family count in this calculation. On SQLite, unset or zero retains the existing disabled resource mode; positive ceilings retain actual live/unknown-TID accounting. A disabled SQLite gate supplies no implicit process-count bound.

This adds one metadata observation to native-default admission decisions, through existing public resources; no polling cadence, private catalog SQL, helper thread or cache lifetime assumption is introduced. `max_connections` is the API's server maximum; existing reserve math remains unchanged. Positive operator ceilings retain current scope semantics and bypass automatic ceiling lookup. Physical server/role limits remain backend-enforced; this is sampled admission, not a lease or arbitrary task footprint guarantee.

### Quick reference and mappings

In `00-Quick_Reference.md`, remove the `WEFT_SCHEDULER_MAX_FRONTIERS` active-setting row and state alongside admission settings that it has been removed and must be unset. Replace the `WEFT_ADMISSION_MAX_CONNECTIONS` row with:

> | `WEFT_ADMISSION_MAX_CONNECTIONS` | PostgreSQL: unset or `0` uses the backend `max_connections` by default; a positive value sets an explicit ceiling. SQLite: unset or `0` disables resource admission; positive values use actual live/unknown TIDs. Ordinary admission requires ceiling minus global reserve minus coherent measured usage to be positive. No frontier cap, task budget or child count. See [MA-1.8], [MANAGER.18]. |
 In [MF-5]'s implementation note, replace `under bounded frontiers` with `under measured admission and work-conserving family ordering`. Add this plan's backlink to each touched spec. Refresh README and theory clauses that characterize fixed W as part of the current design, retaining the historical reason without making it an invariant. The old plan receives a narrowly scoped supersession note for the cap only.

## Tasks and verification

1. Independent review the plan and exact delta through `skills/call-agent/SKILL.md` with a different family. Disposition every finding, then promote the delta. Record the promotion baseline as parent SHA plus saved exact diff before touching runtime. Stop if the review discovers a new capacity rule is needed.
2. Add firing tests before runtime changes: sixth-and-later selection and launch with ample capacity, ordinary measured denial, and obsolete config rejection. Watch the sixth-selection, sixth-launch, obsolete-config, native-default admission and public >5-process regressions fail on old behavior at the intended assertions. Native-default tests must distinguish configured0 gating from the old bypass; prove 300-to-100 metadata changes update limits, metadata failure defers without reading usage or losing reservation, set_max_connections preserves elapsed/idle wait assessment, explicit ceilings/SQLite skip native stats, and restored PG requests at configured0 schedule existing lane retries; keep a positive override control and SQLite0 mode. Retain child-priority coverage beyond five recovered paths; its ordering is unchanged and no pre-change ordering failure is claimed. Remove the constructor bound and both checks; retain ancestry stacks as lineage bookkeeping. Manager constructs the scheduler without a limit. PostgreSQL `_admission_enabled` becomes backend-aware: true at configured0, unchanged on SQLite; inside the existing usage-observation error boundary, resolve the current public backend ceiling when configured0, update the pure AdmissionController effective maximum without resetting its wait history, then observe coherent U. Keep `_admission_max_connections` as the configured input so automatic metadata stays fresh across backend restarts. Add a narrow controller `set_max_connections` method, not direct private-field mutation. No metadata read is added for positive explicit ceilings or SQLite. Config removal uses the existing removed-setting error machinery and fixes the older budget error's obsolete advice.
3. Retarget cap-dependent tests and inherited-config oracles, preserving their actual custody/lineage/ordering contracts. Do not use a test-only frontier gate, number of parents in a wave, or monkeypatched false usage as a concurrency replacement. The sibling-rendezvous case must now complete. Keep true checkpoint-ambiguity stalls. Retain 100/1000/10000 pending-backlog structural tests; run a live-path-size diagnostic at 8/64/256/300 paths (1024 is diagnostic for the admission-off mode) covering select, retirement and recovery. Report measured cost without optimization in this unit. Expect more live processes in admission-off SQLite fixtures and native-gated PostgreSQL fixtures sharing the physical server. Retain12workers and deadlines; diagnose any deferral-driven failure causally. Report peak observed global U from available admission_state records as sampled evidence, never an exhaustive maximum.
4. Exercise real processes through public client/harness paths. On PostgreSQL, retain the private physical server at max_connections=300 and controlled labeled/unlabeled pressure actors. Replace W1/W5 waves with whole-input finite cases of six and eight enrichment/LLM paths. Each entire known fixture topology plus owned pressure/observer/Manager use must fit below its configured public ceiling; derive this from fixture topology, not a runtime charge. Initially hold preparation, wait ALL input parents, release ALL preparations, wait ALL LLMs, then release ALL leaves and collect every result and terminal fact. Six/eight are the whole input, never dispatch limits. Keep actual external denial -> release -> drain. Remove the old70 gated wave protocol and W-derived metrics: after cap removal it can hold every process needed for future children, outside the new contract. Its viable coverage survives in the finite pressure tests plus existing 100/1000/10000 broker-backed classification/selection tests; do not retain a flaky ungated70 acceptance gate or weaken its timeout. Metrics report whole-input completions, overlap, actual U and throughput, not a universal P*H bound. SQLite proves >5 public live branches/nested results without PG-only actors; existing actual live/unknown-TID admission cases own SQLite denial. Add actual PG native-default discovery/denial evidence using the existing public metadata/usage APIs and a configured reserve that lets controlled saturation fit below physical300. Keep all original deadlines and12workers.

5. Independent completed-work review; refresh reciprocal implementation mappings, lesson, README and theory. Run standard overlapping SQLite12 and PostgreSQL12 suites with unchanged timeouts, full mypy, ruff check/format and doc/traceability gates. Record remaining baseline trace debt honestly; do not waive it or fix unrelated debt. Flip this plan and its index row to completed only when its completion requirements are met.

Run commands from this worktree after sourcing the primary checkout `.envrc`, using its repo-managed `.venv` and explicit worktree PYTHONPATH for spawned workers. Focused tests: `tests/core/test_family_scheduling.py`, `test_connection_admission.py`, Manager family/source/scaling/burst files; `tests/system/test_family_scheduling.py`, `test_family_matrix.py`, `test_family_recovery.py`, `test_measured_admission_pg.py`. Use `-n 12` for all acceptance runs. Full SQLite: `python -m pytest -m '' -n 12`. Full PG: `./bin/pytest-pg --all` with its standard 12 workers. Full mypy command matches root AGENTS; lint/format target the changed tree. Plan/spec metadata and Backstitch run before and after, comparing normalized findings against the baseline.

## Rollout, rollback and out of scope

Remove any explicit `WEFT_SCHEDULER_MAX_FRONTIERS` from deployment inputs before installing new code for any config-reading process, including CLI and new-code workers under an old in-memory Manager. Install and restart Managers together using normal custody-preserving turnover; never allow the obsolete environment setting to break loaders. Before release record configured mode, backend C, reserve R and steady-state U per deployment. If U leaves inadequate operating headroom below C-R, raise that actual capacity issue to the owner without tuning this change. The release is one coordinated current-contract upgrade: drain/quiesce and stop every old-code Manager, CLI/client/producer/worker that can initiate a Manager or launch a child before installing new code; then start fresh new-code processes. Accepted ordinary queue rows carry submitted_by identity, not the Manager config, so normal pending work needs no config rewrite. Do not promise mixed-version in-memory clients. The initial deserialize-only probe was incomplete: a stale key survives generic transport and becomes a rejected explicit override when a plain runtime dict is re-resolved. Extend the literal old-envelope probe through that Mapping boundary and assert the actionable rejection. Do not add a special stale-key reader, runtime drop or permissive alias: [ALT-WT-012] and the overview single-current-contract policy require coordinated producers/consumers. If mixed-version support becomes a requirement, stop and raise that separate reconsideration. No durable-data migration: pending-token and checkpoint wire formats stay the same. Within a current-code Manager turnover, surviving live paths are adopted without trimming; coordinated code upgrades drain old-code actors first. Rollback restores the previous release at normal Manager turnover; preserve live tasks and custody, and do not requeue launched work. Observe fresh public task status, resource pressure, queued age, waiting parents with pending children at measured saturation, and actual completions; collations are delayed history. Global reserve tuning and production changes are separate owner decisions. No monitoring-store, supervisor, task deletion, SimpleBroker or other-agent refactor is part of this unit.

## Review and execution log

Author family: OpenAI. Tier-1 reviewer probe verified and R3 plan review passed. Required reviewer boundary: read-only, this delta at the named baseline; explicit owner-accepted risk is removal of the static frontier progress bound, not permission to alter custody or resource accounting. Findings require IDs, severity and suggested disposition; pre-existing concerns stay observations unless worsened; prefer removing unnecessary work. No implementation may begin before plan/delta review and promotion.

Fresh-eyes review: the two capacity checks are the minimal runtime removal. The scale of live ancestry and the invalidity of the old W-based workload protocol are real couplings; addressed in tasks 3-4. The plan does not claim blocking parents release connections. Independent completed-work review remains required.

## Deviation Log

The public-process RED replay followed the minimal runtime edits; the four owner-boundary REDs preceded them. The original unknown-custody system oracle needed an explicit observation of its actual eligibility decision. Details and fault-copy evidence follow. Worker count, server capacity and timeouts were unchanged.

2026-10-15 reviewer bootstrap: Claude Code 2.1.293 liveness returned PROBE-OK. The runbook's plan-mode-only write probe declined without a tool attempt, so that result was not counted as enforcement evidence. Its documented `--restricted --strict-mcp-config --tools Read,Grep,Glob` invocation exposes no write, edit, command, or MCP tools; the write probe confirmed those tools absent and left no file. The Tier-1 plan review uses that constrained tool set plus plan mode, 540-second timeout and closed stdin. Grok 1.0.50 was also tried with the documented OS read-only sandbox; it exited 1 before model startup because `/var/run/docker.sock` resolves outside its socket deny list. No unenforced retry was used. Logs and exact brief/commands: `/tmp/weft-cap-removal-review/`.

Baseline live-path diagnostic (not acceptance, no runtime edits): 8/64/256/1024 recovered independent paths took 0.011/0.129/1.710/27.699 ms to normalize and 0.007/0.123/1.783/29.682 ms for one retirement. This exposes the existing pairwise ancestry comparison once W stops bounding normal populations. Task 3 requires characterizing this live-path dimension without changing the resource policy. Plan/spec metadata gate: six tests pass at 12 workers.

## Tier-1 plan review R1 dispositions

Reviewer: Anthropic Claude Code 2.1.293; constrained invocation recorded above; exit0 after a bounded 540-second budget. Verbatim findings follow. Author dispositions apply to the operative plan body above:

| ID | Disposition | Action |
|---|---|---|
| P1-1 | accept | Added 02/12 source contracts and exact background, bounded-W, pipeline and MANAGER.16 clause replacements; keep checkpoint wire name |
| P1-2 | accept; owner answered | Owner required MAX_CONNECTIONS minus RESERVE. The exact native-default amendment now makes configured0 automatic on PostgreSQL, preserves positive override and SQLite0 behavior; independent review of this amendment precedes implementation |
| P1-3 | modify | Split six-path controlled finite-envelope proof from ungated70 empirical stress; no gated waves, enlarged physical server, timeout change or surrogate runtime cap |
| P2-1 | accept | Remove obsolete deployment input before installing new config readers; coordinated Manager turnover; existing generic transport behavior probed directly |
| P2-2 | accept | RED required only where old behavior is wrong; priority-after-recovery is retained coverage, not an invented old ordering failure |
| P2-3 | accept | Concrete small live-path diagnostic, measured only, no speculative optimization |
| P2-4 | accept | PG owns labeled/unlabeled pressure; SQLite actual live/unknown-TID tests own its denial boundary |
| P2-5 | accept | Selection wording says when selected; explicit headroom condition and saturation watch; ALT-WT-001 current consequence/reconsider condition updated with theory slice |
| P2-6 | accept | Task3 explicitly preserves12workers/deadlines while diagnosing newly exposed admission-off concurrency |


## R2 dispositions and native-default amendment

2026-10-15 owner resolved the default question: use backend MAX_CONNECTIONS minus RESERVE and measured usage. The operative exact delta above now makes configured0 automatic on PostgreSQL, preserves positive override and SQLite0 semantics, and uses the existing public API. This supersedes R1/R2 statements about preserving PostgreSQL admission-off. No new opt-out flag is introduced.

| ID | Disposition | Operative action |
|---|---|---|
| P1-4 | modify | Require coordinated current-contract release of all old-code launch/Manager initiators; test old serialized config -> plain Mapping rejection. Reject special runtime key dropping because it crosses [ALT-WT-012]. Old deserialize-only result no longer supports a safe-mixed-version claim |
| P2-7 | accept | Remove ungated70 as an acceptance gate; finite6/8 controlled pressure proof plus existing large backlog tests preserve the actual claims without betting on future headroom |
| P2-8 | accept | Corrected lowercase sibling clause to when selected; acceptance explicitly limited to bounded-input finite proof |
| P3-9/O-1 | accept | README active-cap/disabled-mode inventory named, QR replacement exact; no irrelevant lineage renaming |
| O-2/O-3 | note | Theory current-account update remains Task5; orphans remain in measured U |

R3 review must include native default, fresh metadata lookup failure, wait-history preservation, current-contract rollout and revised pressure oracle. Runtime and tests remain unchanged until its approval and spec promotion.

## Tier-1 R3 approval and dispositions

Reviewer: Anthropic Claude Code2.1.293, same constrained read-only invocation; PASS, exit0. Accepted R3-P2-1 through P2-4 into operative Tasks2/3 and rollout above. R3-P3-5: use validated API max_connections without duplicate schema checks or widened catches. R3-P3-6: decline optional new log field; internal_limit already exposes C. R3-P3-7: clarified current-code adoption versus quiesced code upgrades. Native role/server limits remain an explicitly reported existing soft-guard limit; no new private role meter or reserve policy. Complete verbatim R3 report follows historical R1/R2 reports.
<details><summary>Verbatim independent review R1</summary>

**Verdict: BLOCKED.** All three blockers are fixable in the text. None of them needs a cap or a new mechanism.

- **Can it be implemented confidently?** Not yet. The exact spec delta is incomplete (P1-1), and the 70-item PostgreSQL test plan can't be run as written (P1-3).
- **Does it degrade the system beyond what the owner accepted?** Only in the admission-disabled configuration (the default), which the plan never mentions (P1-2). Elsewhere it stays inside the accepted tradeoff.

## Verified premise and citations

- **Causal premise holds.** At `weft/core/family_scheduling.py:267`, `select()` returns `None` once `len(_stacks) >= max_frontiers`. At `:307`, `launched()` raises "launch requires a spare frontier". With `AdmissionController(300, .1, True)` at used=33 (`admission.py:38-50`): reserve = max(30, 3+1) = 30, public limit = 270, so the public lane is allowed with 237 available. The ops-thread report itself is outside the repo, so I couldn't check it.
- **With both checks gone, `select()` returns work whenever anything is pending.** A pending row is either a tip child, in an inactive root family (`_oldest_independent`), or in an active root family (the sibling-borrow loop). There is no hidden replacement cap:
  - `MANAGER_FAMILY_CLASSIFY_PAGE_SIZE` limits classification pages, not live paths.
  - The single launch worker and `_child_launch_started_this_turn` limit how fast launches happen, not how many paths exist.
- **Custody gates don't depend on the cap.** `_scheduler_undurable` / `_startup_feedback_tid` / `_admission_evidence_unavailable` gate dispatch at `manager.py:5007-5012` and `:4853-4858`. The successor marks unreceipted launches undurable at `:2480`. So the stall in `test_postspawn_no_pid_checkpoint_preserves_unknown_frontier_and_stalls_new_root` survives cap removal; only its docstring and wire assertions mention W1.
- **When admission denies, the reactor doesn't spin.** Blocked lanes are skipped in `_has_pending_messages` (`:4881-4883`).
- **Constructor and removed-setting machinery are as described.** The scheduler is built at `manager.py:611-612`. `REMOVED_WEFT_CONFIG` (`_constants.py:2930`) is checked against env and override. Its budget message still says "use … WEFT_SCHEDULER_MAX_FRONTIERS", so the plan's fix there is needed.
- **No durable data carries config.** `submitted_by` is only `{tid, context}` (`_spawn_submission.py:380`). The checkpoint key `scheduler_frontiers` can stay as it is.
- **Cited codes exist:** [WT-2], [WT-4]/4.2, [ALT-WT-001], [WT-7], [REV-WT-003]. [MANAGER.18] contains exactly the sentence and the two paragraphs the plan names (`07:1083/1085/1089`).

## Findings

**[P1-1] The exact spec delta misses several current cap/W claims, so "Current specs … describe the same contract" can't be met by promoting the delta as written.**
Text outside the replaced spans that still states the cap or its consequences:
- `02-TaskSpec.md:28` repeats the old background paragraph word for word ("requires a free frontier", "finite frontier bound excludes background residents").
- `12-Pipeline_Composition_and_UX.md:108` says "Ordinary descendants must remain eligible when all ordinary frontier places are occupied…".
- `05-Message_Flow_and_State.md:916` and `07-System_Invariants.md:417` say "candidate selection scans only the bounded W frontiers". After this change that complexity claim is false.
- `07:1066` ([MANAGER.16]) says "ordinary bounded FIFO/LIFO frontier selection".
- Lower priority: "frontier" wording at `03:121`, `03:486`, `03:503`, `05:84/1204`, `05:918`/`07:377` ("finite frontier where applicable").

*Suggested disposition:*
- Apply the Background replacement verbatim to `02:28`.
- In 12:108, delete the last sentence and say "finite scheduling parent" instead of "ordinary frontier ancestor".
- Replace the W-scan sentence with "candidate selection is in-memory and proportional to live paths, without backlog-sized broker reads".
- Reword MANAGER.16 to "ordinary family selection".
- Add 02 and 12 to Source specs.
- State that the `scheduler_frontiers` key name stays, to avoid unnecessary wire churn.

**[P1-2] With the resource gate disabled, nothing bounds process creation any more, and the plan doesn't say so.**
- `WEFT_ADMISSION_MAX_CONNECTIONS_DEFAULT = 0` (`_constants.py:1821`). That makes "disabled" the default for SQLite and for any PostgreSQL deployment without a ceiling.
- Today the frontier bound is the only process bound in that mode: QR row 189 says it "Bounds process growth, independently of resource admission", and the 2026-10-14 plan (line 92) deliberately kept it.
- The owner's reasoning ("available = max - reserve - used") assumes a configured max. With W gone, N independent roots become N live processes, held back only by the startup-feedback gate.
- The new sentence "does not disable family classification or launch custody" implies this without saying it.

*Suggested disposition (no cap):* get explicit owner acknowledgement. Then add one sentence to [MA-1.8] and [MANAGER.18], plus README and QR, along these lines: "With the resource gate disabled, ordinary launches are limited only by startup feedback and custody; operators needing a bound configure `WEFT_ADMISSION_MAX_CONNECTIONS` (on SQLite this counts live/unknown TIDs)." Also add a rollout check that every PostgreSQL deployment sets a ceiling. This blocks only until it is acknowledged and written down.

**[P1-3] Task 4's plan for the 70-item PostgreSQL proof can't work as written, and the gated form would demonstrate the accepted deadlock.**
- "Sized so every required path fits" means 70 × `FIXTURE_PATH_CONNECTION_BOUND` (3·2·4 = 24) = 1680 connections. `bin/pytest-pg:231` caps each server at `max_connections=300`.
- If C stays smaller and enrichments stay gated (`family_workload.py:122-150`: an enrichment waits for its release, then blocks on `child.result`), then without W the Manager fills C with waiting enrichments. The leaves then starve until `FAMILY_WAIT_SECONDS`. That is a hang, not a proof.

*Suggested disposition (removes work):*
- (a) The gated test showing more than five concurrent paths should use 6–8 paths. 6×24 + outside + 2·4 + reserve fits within 300; confirm against the measured `outside`.
- (b) Keep the real labeled/unlabeled denial → release → drain sequence, but on a small workload sized to the envelope, not 70 gated waves.
- (c) Let the existing 100/1000/10000 structural tests carry the backlog-scale claim.
- Drop the `W`/`P*H` fields from `family_progress_metrics`.

**[P2-1] Rollout ordering is wider than "before new Managers start".**
- `_resolve_weft_config` rejects the removed name for every process that loads config: CLI clients, and workers started from new code on disk under an old in-memory Manager. So the env var must be removed before new code is installed anywhere.
- Separately, an old Manager sends serialized config JSON that still contains `SCHEDULER_MAX_FRONTIERS` to new-code children (`launcher.py:208` → `resolve_runtime_config(str)` → SimpleBroker `deserialize_config`; also `manager_process.py:74` and `runtime_identity.py:207`). I couldn't check how `deserialize_config` handles an unknown key, because SimpleBroker is outside the sandbox.

*Suggested disposition:* either state "install and restart Managers together", or add a one-line test that restores stale serialized config.

**[P2-2] One "firing" test can't fire on the assertion it targets.** The ordering code doesn't change. At baseline, a child-priority test with more than five paths fails during setup with `ValueError: launch requires a spare frontier`, not on the ordering assertion. Keep it as a regression test and drop the "watch it fail" requirement for it. The real firing tests are:
- sixth `select` (fails at `:267`);
- sixth `launched` (fails at `:307`);
- config rejection (at baseline `load_config` accepts the setting, so `pytest.raises` fails);
- a system test with more than five live parents under default config.

**[P2-3] Make the live-path cost probe concrete and small.** The costs W used to hide:
- `retire()` is roughly quadratic in live paths per exit (`family_scheduling.py:327-333`).
- `finish_recovery` is quadratic in recovered residents (`:355-363`).
- `_oldest_independent` pops and re-pushes every active root on each `select` (`:239-254`).
- The checkpoint `stacks` grow O(S·H) per publication (`manager.py:1142`).

Probe at S ≈ C (about 300), and larger only if P1-2 is accepted as unbounded. Measure only; don't optimize.

**[P2-4] Task 4's SQLite oracle is mis-specified.** "Denial under real owned external labeled/unlabeled sessions" only exists on PostgreSQL. On SQLite, used = live/unknown TIDs. Either name the SQLite denial oracle (live TIDs ≥ C−R) or limit the pressure sessions to PostgreSQL.

**[P2-5] The new progress text overstates sibling priority and should name the known failure mode.**
- "A sibling starts another path whenever measured admission … allow it" reads as priority. In the code, siblings only come after every independent family. Change "whenever" to "when selected".
- The [ALT-WT-001] incident (enrichments holding connections while their LLM children starve) is the accepted risk returning, only partly offset by child-first ordering. Update [ALT-WT-001]'s current-consequence text and its reconsider condition.
- Make the acceptance item "actual rollup/enrichment/LLM work completes" conditional on the stated envelope.
- Add a rollout watch for waiting parents with pending children at saturation.

**[P2-6] Expect more processes in the full test suite.** Tests that run with admission disabled and submit more than five independent tasks will now run them all at once under `-n 12`. That is the plan's own stop condition, not a reason to cut parallelism. Flag it in Task 3 so failures are diagnosed by cause rather than by extending timeouts.

## Unnecessary work to drop

- The 70-item gated wave protocol (P1-3).
- The "watch fail" step for the child-priority test (P2-2).
- Any optimization before the probe in P2-3 shows a measured problem.

## Out-of-scope observations (not actionable here)

- Under saturation, PostgreSQL `wait_phase="stalled"` diagnostics will become routine. Before, a full W meant no candidate and no admission check. This is diagnostic only (`manager.py:2716`), but ops dashboards may need to read it differently.
- The selection text's "oldest unassigned sibling subtree" is really the smallest pending ID anywhere in the family. That predates this change.
- `retire` and recovery can already adopt more paths than W; removing the cap makes this consistent.

**Reconsideration requests:** none. The plan keeps [IMPL.8-11], [QUEUE.7-8], [OBS.6], the custody and usage clauses of [MANAGER.18], and [ALT-WT-002/015/016/019]. Its only reversal is the fixed-frontier rule and proof the owner authorized, applied to [ALT-WT-001] and [REV-WT-003].

</details>

<details><summary>Verbatim independent review R2</summary>

**Verdict: BLOCKED.** There is one new P1. It is a mixed-version config path that the transport probe stopped one step short of. Fixing it needs a test and a narrow change to how the removed name is rejected. It needs no cap and no new mechanism.

- **Can it be implemented confidently?** Almost. P1-4 must be resolved first. P2-7 would leave an acceptance test that can't be passed reliably.
- **Does it degrade the system beyond what the owner accepted?** Only through P1-4: a new-code Manager whose config came from an old process would fail every launch. Otherwise the plan stays within the accepted removal.

## Disposition verification

| R1 ID | Status | Evidence |
|---|---|---|
| P1-1 | **Verified** | Every exact old-text span matches live text exactly once: `05:916` and `07:417` (W-scan), `12:108` (both sentences), `07:1066` ([MANAGER.16]), `02:28` (background), `03:427/429/435/437/439/441`, `07:1083/1085/1089`. Source specs now list 02 and 12. The `scheduler_frontiers` key name is kept (`05:914`, `07:375`). |
| P1-2 | **Verified (MODIFY)** | The disabled-mode sentence is placed in both [MA-1.8] and [MANAGER.18]. "(the current default)" is true: `WEFT_ADMISSION_MAX_CONNECTIONS_DEFAULT = 0` (`_constants.py:1821`). The SQLite clause is accurate. The rollout plan verifies the PostgreSQL ceiling policy. No default change is assumed. See O-1 for a wording gap. |
| P1-3 | **Partially verified** | Finite6 is sound: the six-path topology (6 × `FIXTURE_PATH_CONNECTION_BOUND` = 144, `test_measured_admission_pg.py:53`) plus the counted actors can fit under physical 300. Simultaneous release removes the wave protocol. The ungated 70-item test still lacks a pass condition (P2-7). |
| P2-1 | **Not verified** | See P1-4. Ordering env removal before install, and turning Managers over together, are correct but not enough. |
| P2-2 | **Verified** | Task 2 lists the four real firing tests; child priority is retained coverage only. |
| P2-3 | **Verified** | Sizes 8/64/256/300 (1024 diagnostic) cover select, retire and recover; measure only. The baseline numbers are recorded. |
| P2-4 | **Verified** | Pressure actors run on PostgreSQL only; SQLite's denial oracle is the existing live/unknown-TID admission tests. |
| P2-5 | **Not applied** | See P2-8. |
| P2-6 | **Verified** | Task 3 keeps 12 workers and the deadlines, with causal diagnosis. |

## New findings

**[P1-4] Removing the setting can make every launch fail on a new Manager whose config was restored from an old process's JSON.**

The path in live code:
- `_resolve_weft_config` rejects removed names in both `env` and `override` (`_constants.py:2955-2958`). That check runs before the `IGNORED_CONFIG_INPUTS` filter (`:2968-2972`).
- `resolve_runtime_config(Mapping)` adds the `WEFT_` prefix to every key and passes them as `override` (`:3029-3031`).
- Tasks store a plain dict, `self._weft_config = dict(resolved_config)` (`tasks/base.py:314`).
- The Manager passes that dict to `launch_task_process(config=self._weft_config)` (`manager.py:1541`), which calls `resolve_runtime_config(config)` on it (`launcher.py:184`).

In old code `SCHEDULER_MAX_FRONTIERS` is a declared field with default 5 (`_constants.py:2735`). So every old-process serialization contains it, **even with the env var unset**. Unsetting deployment env therefore doesn't remove it.

Suppose a new-code Manager is spawned from old in-memory code: an old Manager's turnover, or an old CLI's `manager_process.py:42/74` path. It restores the field as inert data, which is what your probe showed. If `dict(Config)` yields that undeclared key, the next ordinary launch becomes `WEFT_SCHEDULER_MAX_FRONTIERS` in `override` and raises `ValueError`. That repeats for every child.

The probe stopped at `deserialize_config`. It didn't follow the field through dict → Mapping re-resolution. `tests/core/test_runtime_identity.py:97` already exercises this exact dict-into-`launch_task_process` shape.

*Suggested disposition (minimal):*
- Extend the existing probe into a regression test. Restore the old literal envelope, then `dict(...)`, then call `resolve_runtime_config` / `launch_task_process` on it.
- If it fails, apply the removed-name rejection only to ambient inputs (env/TOML/explicit `load_config` overrides). On the runtime Mapping re-resolution path, drop the stale key quietly.
- No new reader. The rejection stays loud wherever operators set config.
- Remove "No new compatibility reader is needed" from the plan, or make it conditional on that test.

**[P2-7] The ungated 70-item stress test has no defined pass condition.**

With no cap, ungated prompt descendants, a single launch worker and startup-feedback pacing, it is a race whether each child request is classified before the next root is selected. Under a ceiling at or near physical 300, the parents can fill C−R, which is the accepted [ALT-WT-001] deadlock. The test would then sit until `FAMILY_WAIT_SECONDS`. As an acceptance test at 12 workers, that becomes a flaky gate, which triggers your own stop condition.

*Suggested disposition (prefer removal):*
- Drop it. Finite6 plus the 100/1000/10000 structural tests already cover the claims.
- Or keep it as a report-only diagnostic outside the acceptance gates, recording completions, overlap and usage, with no drain assertion.

**[P2-8] The P2-5 disposition says "accept", but the operative text didn't change.**
- **Wording:** the Selection replacement still says "a sibling starts another path **whenever** measured admission … allow it". The disposition says "when selected"; change it to that.
- **Acceptance item:** acceptance bullet 3 ("actual rollup/enrichment/LLM work completes") is still unconditional. Scope it to "within the bounded-input finite proof" so it matches the Progress text.

**[P3-9] Some stale cap wording is left outside the exact spans.**

- **README:** `README.md:676`, `:688`, `:739` (which says the frontier bound remains active with admission disabled — now false) and `:1436`. Task 5's README refresh covers these, but `:739` directly contradicts the new disabled-mode sentence, so name it.
- **Other specs:** `03:121`, `03:486`, `03:503`, `05:84`/`05:1204` ("consumes no live frontier"), and `03:431`/`07:377`. These read as lineage terminology, not as a cap. Optional wording cleanup only.
- **Runtime notes:** `REMOVED_WEFT_CONFIG`'s budget message (`_constants.py:2931`) is already in Task 2. `manager.py:612`'s `.get("SCHEDULER_MAX_FRONTIERS", 5)` goes away with the constructor change.

## Observations (not blocking)

- **O-1:** The quick-reference row for `WEFT_ADMISSION_MAX_CONNECTIONS` (`00:188`, "disables the resource gate only") gets no exact replacement text. The plan only says README and QR "must state the same consequence". Exact wording would avoid a second round of drift.
- **O-2:** The [ALT-WT-001] consequence and reconsider-condition update is deferred to the theory refresh in Task 5 and has no exact text. That is acceptable because theory isn't promoted under strategy A. The completion review should confirm it.
- **O-3:** The new Progress text drops "unbounded orphan accumulation" and "bounded retained Manager responsibility". Measured usage still counts orphans, so nothing is lost in substance.

## Unnecessary work to drop

- The 70-item test as an acceptance gate (P2-7).

**Reconsideration requests:** none. [IMPL.8-11], [QUEUE.7-8], the custody and usage clauses, the remaining clauses of [ALT-WT-001]/[REV-WT-003], and [ALT-WT-002/015/016/019] are untouched. Only the cap and its proof are reversed, as the owner authorized.

</details>

<details><summary>Verbatim independent review R3</summary>

**Verdict: PASS.** Nothing here blocks implementation. There are four P2 corrections, which are wording and test additions only, and three P3 notes. None needs a new cap, cache, timer or reader. I made no edits.

**Premise check (what I confirmed in the code)**
- `AdmissionController` (`weft/core/admission.py:78-154`) is pure. The ceiling is used only in `_admission_capacity`, so adding `set_max_connections` that sets only `_max_connections` keeps `_wait_started`, `_last_progress` and `_lowest_usage` intact. A stall only causes logging (`manager.py:2716-2722`); it has no other effect.
- `_observe_admission_usage` (`manager.py:2093-2102`) already turns a PG failure into `None`. `evaluate(used=None)` then blocks both lanes and keeps the PG wait clock running. A failed capacity read inside the same try block will behave the same way, with no admission and the reservation left alone.
- The public `get_connection_stats(queue)["max_connections"]` is already used against the installed extension (`tests/system/test_measured_admission_pg.py:339,376`). I couldn't open the extension's source because the sandbox confines reads to this repo, so this rests on that test usage.
- Rollout claim: `resolve_runtime_config(Mapping)` adds the `WEFT_` prefix to every key (`_constants.py:3029-3031`), and `_resolve_weft_config` checks `REMOVED_WEFT_CONFIG` against the override (`:2955-2958`). So once `WEFT_SCHEDULER_MAX_FRONTIERS` is on the removed list, any old Config turned into a plain dict is rejected. That includes configs where the key holds only the default 5, because old configs always contain it. The plan's "not mixed-version safe" conclusion is therefore correct, and the old-envelope → Mapping rejection test will catch a regression.
- Rollout is coherent with [ALT-WT-012]. I don't think mixed-version support is indispensable, so I'm not requesting a reconsideration.

**Findings**

**R3-P2-1 — the plan misstates `_admission_enabled` (spec wording only).** The plan says PG `_admission_enabled` "remains true at configured0". In the code it is `return self._admission_max_connections > 0` (`manager.py:2058-2061`), which ignores the backend and is false at 0 today. Two places call it: `_admission_allows_queue` (`:2792`) and the retry backstop `_schedule_admission_retry_for_source` (`:2753`). The fix has to check the backend, e.g. `configured > 0 or self._admission_backend_name() == "postgres"`. That also turns on the restored-request retry backstop for PG by default.
*Suggested:* reword the task to "becomes backend-aware: true on PG at 0, unchanged on SQLite". Add a test that a restored PG request at configured 0 schedules the lane retry.

**R3-P2-2 — the full-suite load note is out of date for PG.** Task 3 says "Expect more live processes in admission-off full-suite fixtures". After the amendment, every shared PG test Manager without a configured ceiling becomes gated at `public_limit = 300 − 30 = 270`, measured across the server. All 12 workers sharing the `bin/pytest-pg` container (`bin/pytest-pg:231`) count toward that one number. Peaks between 270 and 300 used to be ignored; now they defer launches suite-wide. The pressure cases themselves are not a risk, because they each use `isolated_postgres_server` (`test_measured_admission_pg.py:308,313`).
*Suggested:* change the sentence so it is admission-off only for SQLite and native-gated for PG. Record the peak global `U` from the 12-worker PG run (the existing `admission_state` log already exposes `used` and `public_limit`). Treat any deferral-driven timeout as a cause to diagnose. Don't use a test ceiling or opt-out to hide it, since none is allowed.

**R3-P2-3 — the deployment check is vague now that the default changes.** "Verify deployed PostgreSQL resource-ceiling policy" doesn't say what to check. A deployment running at 0 today goes from no gating to gating at `C − R`. If the deployment shares a server whose steady-state `U ≥ C − R` (other apps or idle pools), public launches will stop.
*Suggested:* before release, record the configured value, `C`, `R` and steady-state `U` for each deployment. If baseline `U` is within a small margin of `C − R`, take it to the owner as a separate decision rather than tuning it in this change.

**R3-P2-4 — the tests don't yet prove the plan's own specific claims.** Task 2 tests "native-default" behavior in general. It doesn't name tests that would fail if these specific claims broke:
- (a) `C` changes between two observations at configured 0 (e.g. stats return 300, then 100) and `public_limit` follows. This proves there's no stale cache and that the configured value is kept separate.
- (b) Stats fail while usage would succeed: nothing is admitted, the reservation is kept, and the usage read is skipped.
- (c) Calling `set_max_connections` during a PG wait keeps `elapsed` and `idle` (check `_wait_started` is unchanged).
- (d) Positive ceilings and SQLite never call `get_connection_stats` (assert with a spy).

*Suggested:* list (a)–(d) explicitly.

**R3-P3-5 — exception coverage.** The current `except (BrokerError, DatabaseError, OSError, RuntimeError, ValueError)` doesn't catch `KeyError`/`TypeError`. Read the value through the API's validated return. If the implementation also checks the type or positivity itself, raise `ValueError` so the existing boundary handles it. Don't widen the exception list.

**R3-P3-6 — logging.** `_admission_observation` logs only `observed` (`:2099`). `internal_limit` already equals `C`, so automatic capacity is visible. Optionally add `ceiling_source: backend|configured` to the same dictionary. It's cheap, but drop it if you want strictly minimal changes.

**R3-P3-7 — "live paths adopted" wording.** "Existing live paths are adopted without trimming" sits awkwardly next to "stop every worker that can launch a child". After a full drain, adoption only covers leftovers recorded in checkpoints and snapshots, plus residents that can't launch anything. One clarifying clause would fix it. On rollback, old code recovers more than 5 stacks without trimming and only blocks new independent entry. That's acceptable.

**Pre-existing observations (not made worse)**
- O-1: Ordinary roles can never reach `C` because of `superuser_reserved_connections` and `reserved_connections`. So the internal lane (`U < C`) never denies before the backend itself refuses. This existed before for positive ceilings; the existing reserve formula is unchanged, as the owner directed.
- O-2: Whether `usage["total"]` includes PG background workers is unchanged scope semantics.
- O-3: For the isolated native-default denial proof, pick a reserve fraction that keeps `C − R` comfortably below `300 − superuser_reserved`. At `f=0`, `R=4` gives a public limit of 296 against roughly 297 connections an ordinary role can open, which is too tight. The default `f=0.1` gives 270, which works.

**R2 dispositions**
- P1-4 (modified): coherent and explicit as written.
- P2-7: removing the 70-path gate is consistent. The 6- and 8-path tests each cover the whole input on an isolated server, and the existing 100/1000/10000 tests keep the scale claims.
- P2-8: "when selected" is now in both the selection replacement and the lowercase clause.
- The QR row matches the exact text.

No new work beyond the backend-aware enabled check, the controller method, and the one metadata read per observation.

</details>

2026-10-15 spec promotion baseline: parent `601ecb207e657882bb6fb1d834e66596bb13e2e9` plus the exact seven-spec diff saved at `/tmp/weft-cap-removal-review/promoted-specs.diff`. R3 PASS and dispositions precede runtime edits. `git diff -- docs/specifications` reproduces the promotion before later mapping updates. No [IMPL.8-11] or notification/worker requirement was changed.

## Implementation evidence

Spec promotion baseline: source601ecb20 plus exact approved diff saved at
`/tmp/weft-cap-removal-review/promoted-specs.diff`. Before runtime edits, four
regressions failed at the intended assertions with12workers: sixth selection
returnedNone, sixth launch raised spare-frontier ValueError, obsolete setting
did not raise, and native-default PostgreSQL gate returnedTrue atU270/C300.
Exact output: `/tmp/weft-cap-removal-red.log`. Runtime changes are limited to
removing the scheduler's constructor/checks/config and enabling fresh native
capacity through public SimpleBroker metadata plus the existing coherent usage
read. The controller setter preserves drain history.

## Independent implementation review and completion

Reviewer: Anthropic Claude Code 2.1.293, constrained read-only invocation from
`skills/call-agent/SKILL.md`. Initial implementation review and final F1 review
both exited 0 with **no blocker**. The final source passed both full suites.

| Finding | Disposition | Final action |
|---|---|---|
| F1 (P2) | accept with a stronger oracle | Observe the original inline Manager's actual `_queue_has_pending` result after the independent request is indexed, signal an Event, then assert eligibility is false and the exact source row remains. Preserve its driver, supervision, process identity and cleanup |
| F2 (P3) | accept | Add reciprocal capacity/controller/test mappings; replace stale place/cap docstrings with lineage/custody wording |
| F3 (P3) | accept | Name the JSON-to-runtime-Mapping boundary precisely; add no compatibility reader |
| F4 (P3) | accept | Replay the six-parent public test against source `601ecb20` separately; its original 60-second barrier fails with exactly five parents started |
| F5 (P3) | accept | Record payload build/publish costs at 256/300 alongside live-path costs; no optimization or new capacity rule |

F1 investigation: the token-only barrier and a post-turn-only prototype could
still pass with both undurable gates removed. A manual-successor prototype
changed embedding process identity and caused harness cleanup to kill its
pytest worker; it was discarded. Its R2 review was cancelled as superseded.
The intermediate R3 review found no blocker for the post-turn proposal but
explicitly made its firing claim depend on executed fault evidence. The final
predicate observer retains the original harness driver and supervisor. It
creates no thread, performs no broker I/O and never changes the original return
value. The final GREEN passes; the separate fault-copy fails at
`assert readiness and not any(readiness)` with `[True]` and
`unknown custody permitted dispatch`. Exact output:
`/tmp/weft-cap-removal-custody-mutation-final.log`. The assertion concerns
precheck eligibility; the separate dispatch backstop remains covered by the
existing core custody tests. R4 observations F1-O1 through O6 are accepted as
scoped limits or optional diagnostics, with no further mechanism added.

The public-process RED replay followed minimal runtime changes rather than
preceding them. All four owner-boundary REDs preceded runtime edits, and the
public failure was verified against the exact source baseline in a separate
snapshot. No behavioral conclusion relies on an inferred RED.

The first full runs found one metadata omission: the new test module had its
local shared marker but was missing from `tests/conftest.py::_SHARED_MODULES`.
SQLite reported 5,699 passing tests and one policy failure; PostgreSQL stopped
on the same policy failure after 3,840 passing tests. Its catalog entry was
added, then both full suites were rerun. No contention workaround was used.

Final verification:

- SQLite full suite: **5,700 passed, 49 skipped**, 12 workers, 335.64 seconds.
- PostgreSQL full suite: **5,651 passed, 27 skipped**, 12 workers, physical
  `max_connections=300`, 511.48 seconds. Original deadlines unchanged.
- Focused PostgreSQL slice: 126 passed. The final guard-removal mutation failed
  at the intended eligibility assertion; the unchanged guards passed.
- Full mypy: 474 source files clean. Full lint clean; all 502 Python files under
  `weft`, `tests`, integrations and extensions formatted. The repository-wide
  format command still reports the two untouched baseline scripts
  `bin/check-doc-paths` and `bin/check-dom15-fixtures`; byte equality to baseline
  was verified, and they are excluded from this change.
- Backstitch comparison: no new findings. Baseline 31 errors / 1,074 warnings
  remain; infos fall from 677 to 675 through the touched reciprocal mappings.
  This is scoped verification, not a claim of a clean global traceability gate.
- `check-doc-paths`, `check-dom15-fixtures`, plan/spec metadata and diff checks
  pass. Only this isolated worktree's intended files enter the commit; the
  primary checkout's 17 unrelated supervisor/monitor paths remain untouched.

Complete-input PostgreSQL pressure proof, from the final JUnit properties:

| Input / completed | C | Public limit | Saturated actual U | Sampled workload U peak | Sampled task-owner peak | Broker elapsed s |
|---:|---:|---:|---:|---:|---:|---:|
| 6 / 6 | 164 | 161 | 166 | 63 | 6 | 12.961 |
| 8 / 8 | 212 | 209 | 214 | 82 | 6 | 18.010 |

All parents overlap before releasing all preparation gates; all LLM leaves
overlap before releasing all leaf gates. The whole fixture topology fits the
explicit resource envelope; 6/8 are the whole inputs, never dispatch limits.
Sampled peaks are observations, not exhaustive maxima or arbitrary TaskSpec
budgets. Native unset/zero cases separately prove actual public metadata-derived
C=300, ordinary denial at the configured global reserve and progress on release.
No production deployment or tuning was performed.

Measure-only live-path diagnostic under overlapping suites (milliseconds):

| Paths | Recovery | Select | Retire |
|---:|---:|---:|---:|
| 8 | .0053 | .0015 | .0035 |
| 64 | .1303 | .0063 | .1209 |
| 256 | 2.1003 | .0328 | 2.1546 |
| 300 | 2.5533 | .0354 | 2.5023 |
| 1024 | 83.4204 | .1364 | 72.5322 |

Synthetic pre-launch custody records through real Manager payload/publication
owners on isolated SQLite: 256 entries produce 121,207 bytes, build .385 ms and
publish 1.019 ms; 300 entries produce 141,931 bytes, build .434 ms and publish
1.148 ms. These are serialization/write diagnostics, not a live-resource
or arbitrary-depth performance guarantee. Existing pairwise path normalization
remains visible; no optimization or replacement cap was introduced.

<details><summary>Initial independent implementation review (verbatim)</summary>

No blockers. I found one P2 test-oracle gap and four P3 documentation/evidence items. The runtime delta matches the approved plan and spec text.

**Verdict: no blocker.**

## Findings

**F1 — P2 — System custody-stall oracle cannot fire now that the cap is gone.** Suggested disposition: fix (test only).
`tests/system/test_family_matrix.py:444-480` (`test_postspawn_no_pid_checkpoint_preserves_unknown_frontier_and_stalls_new_root`) submits `must-remain-pending`. It then immediately asserts that the row is still in `weft.spawn.requests` and that no `leaf_started` event exists. No barrier proves the successor has had a turn that could have dispatched the request. Before this change the W1 cap blocked that root as a second guarantee. Now the only thing blocking it is `_scheduler_undurable`, populated by `manager.py:2486` and enforced at `manager.py:5021-5025`. If that gate were deleted, this test would almost always still pass.
The core test `test_manager_family_maintenance.py:186` covers the failed-checkpoint gate through the same set. Acceptance item 4 ("unknown launch evidence … still gate ordinary dispatch") therefore has no firing check at system level.
*Minimal fix:* before the absence assertions, wait until the successor has classified the independent request. The positive barrier is that the pending-index token queue `weft.state.labels.<independent.tid>.pending.<independent.tid>` holds its `request_id`; classification continues while undurable (`manager.py:4867-4871`). Then assert the source row is still present. This adds no new primitive.

**F2 — P3 — Reciprocal mapping and nearby implementation notes still describe the old model.** Suggested disposition: fix in task 5.
- The [MA-1.8] implementation mapping (`docs/specifications/03-Manager_Architecture.md:507`) does not list `tests/core/test_work_conserving_admission.py`, `Manager._read_postgres_connection_stats` or `AdmissionController.set_max_connections`. The [SB-0.4] note was updated; [MA-1.8] was not.
- Stale docstrings:
  - `manager.py:4857`: "full frontiers are not reactor work"
  - `manager.py:1552`: "released background places"
  - `family_scheduling.py:281`: "background retains its startup place"
- *Fix:* add the three owners to the mapping. Reword the docstrings to "startup lineage" and "custody gates", with no "full frontier" wording.

**F3 — P3 — The obsolete-envelope test docstring overstates what it proves.** Suggested disposition: reword only.
`test_old_serialized_config_requires_coordinated_process_upgrade` says an old dict "must fail, never silently drop policy." The string boundary accepts the stale key silently: `resolve_runtime_config(str)` at `_constants.py:3012-3015`, reached via `RuntimeIdentity.from_mapping` (`runtime_identity.py:207`). Only re-resolving a plain dict rejects it (`_constants.py:3018`). That matches the approved plan (no special stale-key reader, coordinated upgrade), so the behavior is fine.
*Fix:* make the docstring name the boundary it covers. For example: "a stale key surviving JSON transport is rejected when re-resolved as a runtime Mapping; no reader drops it."

**F4 — P3 — Evidence gap: the public 6/8 test was not run RED.** Suggested disposition: record it as an evidence gap. Optionally run it once on the baseline.
Reading the old code, the test should fire: with the old default W=5, `_wait_events(harness, "enrichment_waiting", 6)` in `tests/system/test_family_scheduling.py:82` would time out after 60s, because the sixth independent selection returned None. That is the same causal check as the recorded "6thselectNone" RED. This is not a behavioral bug.

**F5 — P3 — The live-path cost diagnostic leaves out the cost the cap used to bound most.** Suggested disposition: extend the measure-only report; no optimization.
`_build_tid_state_payload` (`manager.py:1135-1149`) deep-copies every `_scheduler_residents` entry plus every stack. `_publish_scheduler_checkpoint` writes it before and after each launch (`manager.py:1453`, `1472`). Before, finite residents were bounded by W × depth; now they grow with live paths, and in SQLite admission-off mode they are unbounded. The 8/64/256/300 diagnostic covers select, retire and recover, but not checkpoint payload bytes or publish time. If it fails, the failure is fail-closed: a publish failure gates dispatch, then retirement shrinks the payload. *Fix:* add payload size and publish latency at 256/300 to the recorded diagnostic.

## Verified (no finding)
- **No hidden cap remains.** Both runtime checks and the constructor bound are gone (`family_scheduling.py:165`, `253-302`). There is no sentinel, constant or field. `Manager` builds `FamilyScheduler()` with no argument (`manager.py:611`). `_oldest_independent` excludes only active roots, which is ordering, not a count.
- **Ordering is preserved.**
  - Tip-child round-robin with LIFO runs first.
  - Independent families enter FIFO by root ID next, then oldest-sibling sharing.
  - A sibling with no matching tip starts `[*path, tid]`, which `test_sibling_fanout_starts_more_than_five_live_paths` exercises.
- **Custody, startup and notification paths are unchanged.** The `_process_queue_message` gates (`5019-5032`), `_queue_has_pending`, the startup interest and ACK rules are untouched.
- **Native C behaves as specified:**
  - It is read on every observation inside the existing error boundary (`manager.py:2099-2108`).
  - If the stats read fails, usage is not read, the result is `used=None`, and both lanes are blocked (tested).
  - `set_max_connections` does not touch the wait, progress or lowest-usage fields (tested via elapsed/idle).
  - The configured input stays 0 (tested).
  - Positive ceilings and SQLite never read metadata (tested).
  - It uses the existing Queue and public API only: no new connection, no SQL.
- **Config migration works.** The obsolete key is rejected from both the environment and overrides with an actionable message. The budget message no longer points at the retired setting. No backward reader was added.
- **Tests are scoped to the whole input.** Six/eight is the input size only. All-parent and all-leaf barriers come before release. No runtime gets a forecast: the events only echo the configured ceiling. The PG envelope is derived from fixture topology, fits under physical300, and the `count × rollup` overcount leaves at least 48 connections of slack.

## Observations (out of scope, no action required)
- **O1 — Shared PG tests are now gated by default.** Shared tests that set `WEFT_ADMISSION_MAX_CONNECTIONS=0` now run natively gated against the shared PG suite server. Examples are `test_family_scheduling.py`, `test_family_dispatch_scaling.py` and `test_family_matrix.py`. If the 12-worker PG run defers, diagnose it causally. This was foreseen in the plan.
- **O2 — Internal lane now pauses on unknown observations.** On the PG default, an unknown observation also pauses the internal lane, which was never gated before. This matches the promoted spec text ("defers launches").
- **O3 — A ceiling change alone is not logged.** `admission_state` is deduplicated by state, so a changed C without a state transition produces no log line. The rollout step "record backend C" should use a direct `get_connection_stats` query.
- **O4 — Malformed stats are not caught.** A `KeyError` or `TypeError` from a malformed stats dict falls outside the caught set and fails loudly. The public API is typed `dict[str, int]`, so no action.
- **O5 — Envelope actors are not named.** The PG test docstring's "four process bounds" does not name the fourth actor, or whether any Manager-launched internal service is covered. Slack absorbs it; naming it is optional.

No RECONSIDERATION REQUESTED.

</details>

<details><summary>Superseded post-turn prototype review (not final evidence) (verbatim)</summary>

I found no blocker in the F1 oracle fix.

The verdict comes from reading the code only: this test, `tests/helpers/weft_harness.py` (`ensure_foreground_manager`, `_stop_inline_managers`), `process_once` in `weft/core/tasks/base.py`, and the Manager turn, classification and dispatch gates in `weft/core/manager.py`. I ran nothing. Your green and fault-copy runs are still in progress, and the fault-copy result decides the firing claim (F1-O1).

## Findings

None at blocker, high or medium severity.

**F1-R1 (low) — a misleading error if the successor isn't inline.**
- `successor_task = next(... harness._inline_managers ...)` has no default and no message.
- `ensure_foreground_manager` (`weft_harness.py:258-264`) can return an existing registry record without starting an inline manager. If the killed manager's record still looked active, `retained()` would read the old manager's row, which does list the parent as a resident. Then `next()` would raise a bare `StopIteration`.
- That is an error, not a false pass, so it doesn't affect correctness.
- Suggested disposition: optional. Use `next(..., None)` plus `assert successor_task is not None, successor_tid`. Or accept as is.

## Verification of the three requested points

**1. Does the oracle fail when the unknown-custody guards are removed? Yes, assuming the fault-copy confirms it.**
- The two guards are `_scheduler_undurable` in `_queue_has_pending` (`manager.py:4869`) and in `_process_queue_message` (`manager.py:5023`).
- Within one turn (`manager.py:8196-8228`), the order is fixed: refresh scheduler evidence, then `_classify_family_requests`, then the dispatch paths (`super()._process_reactor_turn()` → `_drain_queue` → `_process_queue_message`, then `_drain_public_spawn_requests`).
- The classifier sets the cached candidate to None (`self._family_candidate = None`, `:5522`), so dispatch in that same turn sees the new request as a candidate.
- The watermark (`:5549`) only moves forward to a stamp the classifier has actually read from `weft.spawn.requests`. Nothing else writes that queue during the test window. So `_family_classify_after >= target` means the target was classified in this turn or an earlier one.
- The signal fires only after `original_turn()` returns, so the dispatch attempt in the classifying turn has finished. Without the guards, the request would already be reserved, and `source.peek_one(exact_timestamp=target)` would fail at once.
- **Turns that started before the patch:** a turn already running when the patch is installed can classify and dispatch without the wrapper. The retained watermark makes the next wrapped turn signal, and the source assertion then sees the earlier dispatch. No false pass.
- **Signal timing:** a wrapped turn that classifies before `target_request_id` is assigned can't signal. The next turn signals with the watermark already at or past the target. No false pass.

**2. Is the production worker invariant unchanged? Yes.**
- The delta is test-only. The wrapper runs inside `process_once` on the owner thread, under the existing `_turn_active` reentrancy guard (`base.py:1416-1427`).
- It reads one owner-thread attribute and sets an Event.
- Its only cross-thread state is `target_request_id`, a single-int closure cell.
- It does no broker I/O, adds no thread, and leaves the driver loop and its 10 ms wait unchanged. IMPL.8–11, ALT-WT-001/002/012 and IMPL8-11 are unaffected.

**3. Is harness cleanup safe? Yes.**
- `_stop_inline_managers` (`weft_harness.py:802-820`) works as before: it sets the stop event, calls `manager.stop(join=False)`, then joins.
- Stopping turns go through `_process_stopping_reactor_turn`, which the wrapper doesn't cover.
- The supervisor handle, the harness's process identity, and the registered fixture PIDs are untouched.
- Monkeypatch undoes the patch after the harness exits, which is harmless.

## Observations (not blockers; owner decisions)

- **F1-O1 — what the fault-copy run must show.** The code argument assumes no other gate is closed in the classifying turn: `_startup_feedback_tid`, `_admission_evidence_unavailable`, `_admission_allows_queue`, or an active child launch. If any were closed, dispatch would slip to a later turn and the guard-removed copy would pass. The firing claim holds only if the fault-copy fails at the source-presence assertion, not at the 60 s wait or at `StopIteration`. Suggested disposition: record that failure point in the plan.
- **F1-O2 — the stall check covers one turn.** The test proves the stall at the first turn that has considered the request, not over later turns. A guard that held for one turn and then lapsed would get past this test. This was already true before the fix. Extending it would add work, so this is an owner decision, not a request.
- **F1-O3 — reliance on private state.** The test reads `_inline_managers` and `_family_classify_after`. Other tests already do the same (`tests/test_harness_registration.py`, `tests/core/test_family_dispatch_scaling.py:158`). No action suggested.

No reconsideration is requested.

**Result: no blocker.**

</details>

<details><summary>Final F1 eligibility-oracle review (verbatim)</summary>

No blockers. No RECONSIDERATION REQUESTED.

This is a read-only review: I ran nothing and modified nothing. Your RED and GREEN runs are still in progress, so nothing below rests on executed results.

## Findings

None at blocker level. Everything I found is an observation, listed in the observations section below.

## Why the oracle is sound

- **The wrapper actually takes effect.** `monkeypatch.setattr` puts the wrapper on the instance, and Python finds an instance attribute before a class method. So every `self._queue_has_pending(...)` call goes through the observer, including the calls from `_has_pending_messages` (`weft/core/manager.py:4897`) and the base watcher (`multiqueue_watcher.py:1307,1335,1347,1441,1509`). `original_pending` is the bound method captured before the patch, so the real method runs unchanged.
- **The observer does no I/O and creates no thread.** It reads `queue.name`, `_queue_names["inbox"]` and `FamilyScheduler.pending()`, which is a plain dict lookup (`weft/core/family_scheduling.py:186-187`). It always runs on the inline driver thread, and `Event.set()`/`wait()` makes the `readiness` list safely visible to the test thread.
- **The target does get indexed in the GREEN run.** `_classify_family_requests` keeps running while admission or custody gates are closed (`weft/core/manager.py:5524-5532`), so `pending(target)` becomes non-None.
- **No false RED from the classification flag.** Inside the custody-gated branch, `_queue_has_pending` returns `_family_classification_pending` (`manager.py:4866-4871`). That flag is only True when a full page was read (`manager.py:5550-5552`). With one row it is False, so a legitimate GREEN never records True.
- **The RED is deterministic.** With the custody-gate term removed, the first evaluation after indexing goes on to `_peek_family_candidate()`. That runs before anything is reserved, so `pending(target)` is still set and the True gets recorded no matter when reservation happens. This matches your fault-copy diagnostics.
- **Process identity and supervision are unchanged.** The test uses the original inline Manager and `_serve_inline_manager` driver (`tests/helpers/weft_harness.py:304-318`). 12 workers, the 60 s `_wait`/`considered.wait` and the 180 s module timeout are all as before.
- **Cleanup is unaffected.** `monkeypatch` undoes the patch only after `WeftTestHarness.__exit__`, so the observer is still installed while `_stop_inline_managers` runs (`weft_harness.py:802-820`). It cannot raise or block there: the inbox branch of `_queue_has_pending` does no I/O while gated, and the observer only does a dict lookup.

## Observations

| ID | Severity | Observation | Suggested disposition |
|----|----------|-------------|-----------------------|
| F1-O1 | Low | The message "unknown custody permitted dispatch" overstates what failed. The assertion checks the precheck, which is stricter than dispatch. If only the precheck term were removed, the dispatch guard at `manager.py:5020-5025` would still block the launch, yet the test would fail. That failure is correct under [MF-6] ("Retained launch custody does not create actionable reactor work"); only the wording is off. | Accept. Optionally reword to "unknown custody reported actionable inbox work". |
| F1-O2 | Info | `readiness` is checked once. A False followed later by True would go unseen. Under unknown custody nothing settles, so the GREEN run has no legitimate path to that sequence, and the RED fires on the first evaluation after indexing. | Accept. No settle window needed. |
| F1-O3 | Info | Removing only the dispatch guard (`manager.py:5020-5025`) is not reliably caught here. While the precheck returns False, `_update_active_queues` never activates the inbox, so that guard acts as a backstop. | Accept. Out of F1's scope; don't widen the test. |
| F1-O4 | Info | Evaluations that happen between `client.submit` returning and `target_request_id` being set are ignored. In the GREEN run, later evaluations are prompted by the pending-token write's change hint (`manager.py:5512`), or by native activity on PostgreSQL. In a RED where a launch happened inside that microsecond window, the test would still fail, but with the 60 s message "successor did not consider request". | Accept. |
| F1-O5 | Info (improvement) | `next(...)` over `_inline_managers` has no default. If `ensure_foreground_manager` handed back a stale non-inline record, the test now errors with StopIteration. Before, it could pass using the old manager's snapshot. | Keep. Optionally use `next(..., None)` plus an assert for a clearer message. Your call; not a blocker. |
| F1-O6 | Info | `monkeypatch` undo stores the original bound method back on the stopped manager instance, a harmless reference cycle that garbage collection clears. | No action. |

## Checklist

- Accepted risks (uncapped scheduling, native PG C-R-U, SQLite 0 = off, resources retained by waiting parents, coordinated upgrade) are not affected.
- ALT-WT-001/002/012 and IMPL8-11 are not affected: no queue lane, polling cap, compatibility code or new reactor input.
- No production queue, helper or polling changes; no runtime changes.
- Worker invariants hold: 12 workers, unchanged timeouts, no added thread, no observer broker I/O, same process identity.

</details>
