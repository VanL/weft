# Bounded Status Reconstruction and Monitor Work Plan

Status: draft
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-5], Cleanup Boundary; docs/specifications/07-System_Invariants.md [OBS.10], [OBS.11], [OBS.11a], [OBS.12], [OBS.12a], [OBS.13.3], [OBS.13.4], [OBS.13.7], [OBS.13.10], [LIVENESS.R10]; docs/specifications/10-CLI_Interface.md [CLI-1.2.1], [CLI-1.2.3]; docs/specifications/09-Implementation_Plan.md [IP-1.0], [IP-1.1]; docs/specifications/14-Python_API_Surfaces.md [PY-2]
Superseded by: none

Class: 5. Status observation lifetime and monitor execution/cleanup boundaries.
Promotion strategy A: proposed behavior is promoted into governing spec text
before implementation conformance is claimed; synchronize backlinks and ownership.

## 1. Goal and boundary

After the [namespace plan](2026-09-11-per-tid-task-state-namespace-plan.md), remove
repeated reads and computation inside ordinary status calls and monitor work.
Preserve status evidence precedence, destructive-action safety, and public API
shapes. There is no latency gate. Work should scale with the evidence required by
the operation, rather than repeatedly with all retained history.

The namespace plan owns every old-state caller, new queue names, short-ID
resolution, refresh cadence, and retirement policy. This plan depends on that
layout and does not add a compatibility branch. It owns request-local evidence
reuse, incremental snapshot watching, scanner over-read, exact-delete batching,
and scheduler complexity. No new persistent cache, materialized lifecycle queue,
query service, general cache invalidation framework, or broker SQL implementation.

## 2. Baseline and observed work

Inspection baseline: `a1e7d496437545579985f63d2fa130666b64a162`; rebase this analysis
on the namespace implementation before starting. Its caller/spec ledger is a
required companion. L is the retained global task log; L_t is retained global evidence at/after the selected task's birth-bound lower
timestamp (including unrelated newer tasks); H is flat mapping history
at this baseline; N is retained task count; Δ is new log evidence since a cursor.

Real-broker instrumentation with eight completed tasks, three lifecycle events
and three mapping rows each found the following. These counts include JSON
reconstruction, not just queue access:

| Operation | Baseline task-log rows read | Baseline mapping rows read |
|---|---:|---:|
| System status, including running filter | 48 (two scans) | 48 (two scans) |
| Task list | 24 (one scan) | 24 (one scan) |
| Oldest task snapshot | 72 (three scans) | 24 |
| Newest task snapshot | 9 (three short scans) | 24 |
| Oldest task with process details | 72 | 48 |
| Oldest task by short ID | 72 | 48 |

System status scans separately for task records and manager-spawn/service
projection. Known-task status reads TaskSpec for pipeline detection, reconstructs
status, then reads TaskSpec again for projection. Python snapshot watching can
repeat that work at each observation interval. Namespace conversion removes H
from point lookup; it does not remove these L_t repetitions.

TaskMonitor already checkpoints ingestion. However, its eager scan window can
read 50,000 rows before the consumer selects a 5,000-row batch. The external-log
retirement pass also reads eagerly, but skipped/young rows mean it cannot simply
use the selected-row batch size as a scan bound. Cleanup's small family budget
currently does not bound earlier global evidence acquisition. LivenessMonitor's
heap update/drop path rebuilds a heap of N entries per TID, allowing quadratic
work; the eight-worker cap does not bound scheduling work.

SimpleBroker's installed oldest-first generator uses OFFSET pagination. A local
SQL-shape probe at 100,000 rows showed about 21 million VM instructions for OFFSET
versus 701,000 for keyset pagination. That is an upstream opportunity, not license
to add direct SQL in Weft. Keep broker work and Weft decoding counts separate in
benchmarks. This plan improves Weft even without that upstream change.

## 3. Complete operation and preservation matrix

The companion namespace ledger covers all transitive runtime-state callers.
The following enumerates status modes and the additional monitor owners touched
here. Re-run references and adapter call graphs before editing; do not assume a
CLI mode and a similarly named Python method share observation semantics.

| Surface / owner | Required behavior and work |
|---|---|
| `weft/cli/app.py::status_command`; `weft/commands/system.py` status collector | Plain/JSON, default/all/status filters share one task-log traversal and one runtime snapshot collection. Feed both task and manager-spawn reducers from each event; final task records do not contain all manager-spawn evidence. Preserve service diagnostics and terminal filtering |
| `weft/commands/tasks.py` task list and stats | Same task listing semantics and presentation-only stats; reuse request collection, do not replace task listing with queue names |
| `tasks.py` task status / snapshot / terminal snapshot | Resolve once, retain `CollectedTaskSnapshot.taskspec_payload`, use it for pipeline detection and projection. One selected-task lifecycle reconstruction; preserve pipeline precedence and Monitor fallback |
| Task status `--ping`, `--process`, full/short TID | PING remains explicit and live; process output reuses only this call's snapshot. Short resolution remains names-only after namespace plan. Never substitute saved display evidence for later control authority |
| `system.py::_iter_public_status_events`, `events.py::iter_task_events`, `follow_task_events`; CLI status/task status `--watch` | Existing incremental event streams stay separate; no snapshot-watcher conversion |
| `tasks.py::watch_task_status`; `client/_namespaces.py` tasks.watch | Iterator-local incremental reconstruction described below; output cadence, timeout, cancellation, terminal stopping and public types preserved, with explicit evidence-retention delta |
| `events.py::_task_snapshot_event` and realtime stream callers | Each requested snapshot gets fresh call-local evidence. Do not retain one request cache for the entire realtime stream |
| `client/_namespaces.py`, `client/_task.py`; command result / `_result_wait` / `_streaming` / task-evidence callers | Shared capability parity; Python system.status retains its different default filtering; result/terminal waits keep fallback and output semantics |
| `tasks.py` stop/kill/control escalation and `_ControlSurfaceResources`; `_spawn_submission.py` | Consume improved point readers but retain fresh escalation reads, identity checks, routes, and spawn evidence order. No cross-attempt cache |
| `core/monitor/task_log_scanner.py`; `task_monitor.py` ingest, precheckpoint recovery, raw external retirement | Bound eager work without advancing a checkpoint across unprocessed evidence; preserve highwater, error, age and external-output confirmation gates |
| TaskMonitor stale-open/service, terminal/reserved/dead runtime cleanup; `core/pruning/runtime.py` | Use namespace plan candidate reads; skip empty requests and share evidence only within one decision scope. Preserve policy ownership, protected sets, unknown handling and missing-state residue discovery |
| `core/pruning/apply.py` and LivenessMonitor exact deletes | Batch selected IDs by queue, using public broker operations; preserve verified absence before recording retirement and namespace older-before-latest ordering |
| `core/tasks/liveness_monitor.py` due scheduling | Remove full heap rebuild per update, retain bounded memory and one logical due item per TID; generation/token checks and cap remain |

CLI registration is in `weft/cli/app.py`; task status is owned by `task_status`
and task listing by `task_list`.
Follow imported capability owners; do not add another adapter.

## 4. Concrete changes

### 4.1 One evidence collection per request

Pass already collected mappings and TaskSpec payload through existing internal
collectors, with optional private parameters or a small existing result value.
Do not introduce a general session/cache object. System collection drives task
and manager-spawn reducers in one replay and then renders services from those
results. Candidate-only operations stay candidate-only. Output enrichment must
not silently fetch the same lifecycle evidence again.

Keep error/fallback behavior: a missing or unavailable Monitor store still falls
back as today; runtime evidence supplements lifecycle evidence; pipeline status
wins where it currently wins. Mapping freshness applies to one collection, not
an indefinite sequence of calls. A fresh public call is still a fresh observation.
Both `_latest_pipeline_status_snapshot` and `_pipeline_task_snapshot` must accept
the already collected/folded TaskSpec on optimized paths instead of invoking
`load_latest_taskspec_payload` again. Watcher pipeline refresh uses that retained
TaskSpec even after its raw log row retires. Known-TID cold reads may still scan
L_t once because lifecycle events remain in
the global log. Do not claim an O(1) full status operation.

### 4.2 Iterator-local snapshot watching

Bootstrap with the same evidence sources and precedence as `task_snapshot()`,
including a retained Monitor-store record when raw lifecycle rows have already
been retired. A fresh watcher with no raw rows but a Monitor record must yield
the same first snapshot as a fresh `task_snapshot()` call. Use the existing
collector/fallback to seed the iterator; do not create a raw-log-only bootstrap.
Capture the raw-log cursor from the same initial scan used for that collection.
Do not independently sample a later highwater, which could skip events arriving
between the scan and cursor capture. Set the cursor to the maximum raw scanned message ID, including unrelated,
malformed, and non-object rows, as well as selected-task events. Fold only new events into the selected task's
existing record. Retain no raw event history and no all-task map.

For this iterator's lifetime, previously observed lifecycle evidence survives
raw-log retention. A fresh snapshot has current storage visibility and can differ
from the existing watcher after deletion. This is an explicit observation contract,
not a hidden cache optimization. No count-based invalidation, deletion detector,
periodic full replay, or durable cursor is needed. Memory is bounded by the one
folded task record and its ordinary payload/routes, not the number of polls or
raw events. Release the state and subscriptions when the iterator closes.

Refresh runtime state, local terminal/output evidence, pipeline/service/selected-manager evidence,
and time-dependent liveness at the existing observation interval even if Δ=0.
If bootstrap lacks sufficient lifecycle evidence, preserve existing Monitor/log
fallback behavior until a record can be established. Unknown remains unknown;
never synthesize terminal completion from disappearance. Preserve caller timeout,
terminal exit, and context ownership; iterator close or consumer interruption
releases resources. No cancellation parameter is added. CLI event watchers remain
unchanged because their contract already concerns events rather than snapshots.

### 4.3 Bound monitor reads and exact deletes

For ingestion where every scanned row consumes the batch, request at most
`min(scan_limit, batch_size)` usable rows, with at most the existing one-row
lookahead needed to distinguish exhaustion. Keep batch-full versus scan-limit
stop diagnostics accurate and checkpoint only applied evidence. Lookahead proves
whether work remains; reaching the smaller requested window is not by itself
the configured scan-limit fence. Pin exactly `batch_size` and `batch_size + 1`
rows with tests for both `stop_reason` and `completed_high_water`, plus the case
where the configured scan limit is smaller than the batch size. Scanner acquisition and iteration failures must continue to propagate to the
pass owner. The current real Queue iterator is lazy, so backend failures already
escape its generator-construction guard. Preserve that behavior in the bounded
reader and test acquisition, first-read, and later-page failures: none may advance
the checkpoint or claim a completed highwater for summary/disposition. Preserve the distinct full-highwater fence for
summary decisions and independent per-family cleanup fences.

For precheckpoint recovery and external retirement, skipped rows do not consume
the selection budget. Iterate in bounded pages and stop when either the original
scan budget or selected batch budget is exhausted. Preserve oldest-first external
retirement and its age/output-success requirements. Do not add a durable cursor
that skips retained rows. Close iterators before deleting to avoid pagination
mutation problems. Keep scan counters as actual inspected rows, not requested
limits. A page of bounded lookahead is acceptable; preloading the entire remaining
50,000-row window is not.

Do not acquire state/service snapshots when a candidate set is empty. Reuse the
same service evidence within one policy decision, but refresh between destructive
phases when current safety depends on it. Candidate formation must not starve
eligible later candidates by truncating a protected prefix.

`apply_exact_prune_candidates` already groups exact IDs and supports missing-row
reconciliation; retain it for pruning and extend only paths still deleting one
by one. LivenessMonitor keeps its own custody executor and uses the public queue
batch API with the same verified-absence semantics. Do not add task-state queues
to runtime-prune groups or move custody into the shared reader.
Group exact IDs by queue and delete bounded chunks through existing public
SimpleBroker delete APIs. Reuse existing absence verification in pruning apply;
partial return counts alone do not prove which IDs disappeared. Record cleanup
progress/retirement only after required selected IDs are absent. Preserve
older-before-latest namespace retirement ordering, so a failed older-row batch
cannot expose superseded state. No multi-queue transaction or rollback journal.

### 4.4 Bounded scheduler updates

Use the existing heap with a per-TID current due record and lazy invalidation.
Push/update is amortized O(log N); popping ignores entries that no longer equal the current
due record. Before `next_wait_timeout` peeks at the next due time, discard stale
heap heads by the same validity rule. Share that small head-cleaning operation
with scheduling; do not replace peeking with an O(N) minimum scan. An expired
stale head must not cause zero-time reactor wakeups. There is one logical due
item per TID. Bound stale physical entries:
rebuild from current records when heap size exceeds twice the current-record
count, and clear immediately when that count is zero. Apply the bound after
removals as well as inserts. This gives O(N) retained memory and amortized bounded
updates without a new dependency or custom indexed-heap implementation.

Retain fairness among due tasks, generation checks, and worker capacity handling.
A task left due because workers are full must not cause a busy loop or disappear
from scheduling. Test bursts of simultaneous completions, repeated same-TID
updates, mass deletion, and stale result arrival. The physical-heap bound replaces
any test assuming exactly one physical tuple per TID; it does not weaken the
single-worker or single-logical-schedule rules.

## 5. Spec promotion ledger

Use exact files under `docs/specifications/`. Update backlinks and nearby
implementation mappings only where behavior/ownership text actually changes.
No public signature additions are proposed.

| File and refs | Proposed delta or preservation requirement |
|---|---|
| 05-Message_Flow_and_State.md [MF-5] | Add request-local evidence reuse and the iterator-lifetime retention contract for Python snapshot watching; retain lifecycle/runtime/pipeline precedence and cold fallback |
| 09-Implementation_Plan.md [IP-1], [IP-1.0], [IP-1.1]; 14-Python_API_Surfaces.md [PY-1], [PY-2] | State snapshot-watch retained observations versus fresh snapshots; preserve capability/adapter ownership, cancellation, errors and public types |
| 10-CLI_Interface.md [CLI-1.2.1], [CLI-1.2.3] | Clarify that CLI watch is the existing event stream; normal status/filter/stats behavior remains. No promises that full status is constant-time |
| 07-System_Invariants.md [OBS.10], [OBS.11], [OBS.11a], [OBS.12], [OBS.12a] | Preserve evidence precedence and no invented terminal status; cross-reference iterator observation scope where needed |
| 07 [OBS.13.3], [OBS.13.4], [OBS.13.7], [OBS.13.10]; 05 Cleanup Boundary | Preserve actual processed checkpoints, highwater fences, destructive guards and external-output confirmation; document bounded scan versus selection work and verified exact-delete batching in implementation notes |
| 07 [LIVENESS.R10]; 01-Core_Components.md [CC-2.3] | One logical due record per TID, O(retained TIDs) physical scheduler storage with bounded stale entries; no cadence or deadline change from namespace plan |

Associated contracts to read and preserve: 03-Manager_Architecture [MA-1.6a],
[MA-1.8] for service projection/admission; 04-SimpleBroker_Integration [SB-0.4]
for backend APIs; 07 [OBS.4], [OBS.13.6], [OBS.13.11], [OBS.14–17],
[LIVENESS.R1–R9] for runtime identity/custody;
10 [CLI-1.1.1], [CLI-1.2.2], [CLI-1.3] for spawn/result/control;
12-Pipeline_Composition_and_UX [PL-5.2], [PL-5.3];
02-TaskSpec runtime fields; 08-Testing_Strategy [TS-0], [TS-3.1];
11-CLI_Architecture_Crosswalk command ownership. These are not scope to redesign.

## 6. Reviewable implementation slices

1. **Request-local status reuse.** Verify the companion caller ledger against the
   landed namespace. Add real-broker read/decode counters and status parity cases,
   consolidate log/mapping acquisition, and retain TaskSpec in the existing
   collection result. Promote relevant spec notes and verify CLI/client modes.
2. **Monitor read/delete bounds.** Exercise scanner limits and stop reasons,
   selective scans, empty candidates, exact partial deletion, external output
   failures and highwater correctness. Implement bounded pages and reuse existing
   deletion verification. Review destructive boundaries independently.
3. **Scheduler complexity.** Add operation-count and memory-bound tests; implement
   bounded lazy invalidation. Verify worker saturation and fairness without wall
   clock sleeps. Promote physical versus logical schedule wording.
4. **Incremental snapshot watching.** Promote the explicit observation-retention
   delta first. Add same-scan bootstrap/cursor, Δ folding, and fresh auxiliary
   evidence. Test retention deletion, missing Monitor, pipeline changes and all
   exit paths. Review this semantic change independently from the reuse patch.
5. **Integration and documentation.** Re-run the entire mode/caller matrix, record
   before/after work counts and representative timings, update implementation
   mappings/backlinks and review findings, then run repository required checks.

## 7. Verification

Existing test homes: `tests/commands/test_status.py`, `test_task_commands.py`,
`test_task_evidence.py`, `test_realtime_events.py`, `test_task_monitor.py`,
`test_runtime_prune.py`; `tests/core/test_client.py`, `test_monitor_collation.py`,
`test_monitor_store.py`, `test_monitor_sql.py`, `test_pruning_apply.py`,
`test_monitor_external_log.py`, `test_control_probe.py`;
`tests/core/monitor/test_progress.py`, `test_lifetime_report.py` and policy tests;
`tests/tasks/test_liveness_monitor.py`, `test_task_monitor.py`,
`test_pipeline_runtime.py`; `tests/cli/test_status.py`,
`test_cli_status_rendering.py`. `tests/commands/test_task_snapshot_reducer.py` and
`tests/core/test_task_log_scanner.py` own reducer/scanner coverage. Add tests alongside the owning behavior.

Required comparisons:

- Grow unrelated history while holding one TID fixed: runtime point reads remain
  local; cold lifecycle replay occurs once, not three times. Project status feeds
  both reducers in one pass. Include empty, oldest, newest, filtered, service-only,
  pipeline, degraded Monitor, ping and process observations.
- Snapshot watch bootstrap reads existing evidence once; later log reads are Δ.
  A no-new-event tick still observes changed runtime, terminal/output and pipeline
  evidence. Deleting old log rows does not erase already observed state; a new
  snapshot still follows its documented fallback. Events arriving during bootstrap
  are neither skipped nor double-applied. Cover malformed-only bootstrap and
  malformed tails, manager supersession without a new target event, and a pipeline
  update after the original TaskSpec log row is retired.
- Test scanner budget boundaries and skipped rows; checkpoints never pass failed
  or unprocessed rows. Young external rows and failed external output remain.
  Batched deletion failures cannot advance progress or expose older state.
- N scheduler entries with repeated updates do not perform N full rebuilds per
  sweep; physical storage stays within the stated bound at public operation
  boundaries. Eight occupied workers do not spin or lose due tasks. Stale/superseded expired
  heap heads cannot cause sub-interval wakes when all current due times are future.

Use real broker fixtures plus deterministic clocks and operation counters.
Timings are diagnostic, not acceptance thresholds. Run targeted suites before
full pytest, mypy and ruff with `. ./.envrc` and repo binaries. No production
implementation is claimed by this draft; plan metadata/spec hygiene and diff
checks validate document edits.

## 8. Rollout, rollback, and accepted limits

Land after the namespace cutover. These slices need no persisted schema migration.
Each can be reverted independently, except the watcher spec must be reverted with
its implementation. Restart long-lived clients/monitors to discard in-memory
cursor/scheduler state. Keep namespace custody and exact-deletion rules intact.
Never attempt to restore already retired rows as a rollback mechanism.

Cold lifecycle status still depends on global retained log position. Bulk status
still visits retained tasks. Namespace listing still depends on broker index
behavior. The follow-on deliberately does not solve these with another durable
index. Upstream keyset pagination can be pursued separately with SimpleBroker's
own contract/tests. Consider a further storage change only with evidence that
remaining cold-read cost matters after these simpler changes.

## 9. Review and deviation log

Require independent review of caller completeness, explicit watcher semantics,
cleanup fences and simplification. Findings must distinguish newly introduced
risks from preexisting limitations. Prefer removing unnecessary state over adding
fallback layers. No open-ended cache validation or compatibility machinery.

| Spec ref | Planned behavior | Current behavior | Rationale | Proposal |
|---|---|---|---|---|
| MF-5 / IP-1.1 / PY-2 | Iterator retains observed lifecycle evidence | Snapshot polling reconstructs from available evidence each time | O(Δ) repeated log work with a clear observation lifetime | §4.2, §5 |
| LIVENESS.R10 | Bounded stale physical heap entries | Heap rebuild maintains one physical entry per TID | Remove quadratic updates without unbounded memory | §4.4, §5 |

Author fresh-eyes and independent review dispositions are appended below before
handing off these drafts for implementation.


### 2026-09-11 current-draft fresh-eyes and independent review

Author fresh-eyes pass checked the current draft against actual owner paths,
companion scope, and source/spec boundaries. Independent status and monitor
reviewers inspected both drafts and the owning code. Dispositions:

| Finding | Disposition |
|---|---|
| Failed per-TID custodian reads could look like absence | Adopted: strict reads preserve cached evidence; only successful absence removes it |
| Retirement did not positively match the probed row or handle already-missing older IDs | Adopted: exact newest-valid ID match; verified missing older rows count as complete |
| Scanner failure propagation must survive bounded reads | Preserve existing backend failure propagation; add checkpoint/highwater fault tests |
| Watch cursor could repeatedly scan malformed tails | Adopted: raw scanned message-ID cursor includes malformed/unrelated rows |
| Pipeline helpers could silently replay log during watcher refresh | Adopted: pass retained TaskSpec to both pipeline snapshot helpers |
| Selected-manager refresh omitted | Adopted: refresh it each observation interval and test supersession |
| Inexact owner/test paths and missing cadence spec | Adopted: actual CLI/endpoints/harness paths and LIVENESS.R8 |
| Heap complexity overstated | Adopted: amortized O(log N), with explicit O(N) rebuild and physical memory bound |

These reviews found no need for an additional cache framework, indexed heap,
startup protocol, or persistent observation store. Production validation remains
an implementation obligation; document checks do not establish runtime parity.

Document verification: plan metadata and spec hygiene suites passed (8 tests);
relative plan links resolve and `git diff --check` passed. The namespace draft
preserves its original review record verbatim. These are uncommitted planning
edits; runtime implementation and its tests remain future work.


### Different-family review, 2026-09-11

Claude Opus 4.8 performed a read-only source/spec review of both drafts and
returned PASS for both approaches. It verified that all 13 production files
referencing the current mapping queue are in the caller ledger. Its remaining
implementation gaps were adopted into the drafts before handoff:

| Finding | Disposition |
|---|---|
| Lazy scheduler invalidation omitted raw heap peeking in `next_wait_timeout` | Adopted: purge stale heads before both wait calculation and scheduling; add no-busy-wakeup test |
| Fresh watcher after raw-log retirement lacked an explicit Monitor-store bootstrap case | Adopted: same evidence set/first snapshot as `task_snapshot`, including retained Monitor record |
| Smaller ingestion window could alter stop/highwater flags | Adopted: distinguish configured scan fence from batch lookahead; exact batch and batch-plus-one boundary tests |
| Superseded valid hot-task history also accumulates until reconciliation | Adopted: explicit retention/cost disclosure and hot-publication benchmark case, preserving minimum ages |
| Point subscription may begin before task-state queue exists | Adopted: open lazy exact queue handle and test first-write wakeup from nonexistent namespace entry |

No new architecture or public API was added to resolve these findings. Source
review confirms the approach; runtime correctness remains to be demonstrated by
the implementation tests specified above.
