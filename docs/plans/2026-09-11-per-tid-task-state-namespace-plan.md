# Per-TID Task State Namespace Plan

Status: completed
Source specs: docs/specifications/07-System_Invariants.md [OBS.5], [OBS.6], [OBS.6a], [QUEUE.7], [LIVENESS.R1–R10]; docs/specifications/05-Message_Flow_and_State.md [MF-5], Cleanup Boundary, Queue Lifecycle; docs/specifications/00-Quick_Reference.md queue table; docs/specifications/01-Core_Components.md [CC-2.2], [CC-2.3]; additional caller contracts in §3
Superseded by: none

Class: 5. Spec-changing persistence, observation, and cleanup contracts.
Promotion strategy A: edit the governing spec text before claiming implementation
conformance; update backlinks and implementation mappings in the same slice.
Implementation is complete. The promoted specifications govern behavior; this
document records the implementation and review history.

## 1. Goal and scope

Replace `weft.state.tid_mappings` with `weft.state.tasks.<tid>`. Keep blind,
append-only publication and the existing payload. LivenessMonitor remains the
sole component allowed to delete state rows. Queue names support discovery;
a valid snapshot supports runtime evidence. Neither proves that a process is live.

Known-TID status should read one task's runtime state, without decoding everyone
else's JSON. Enumerating namespace names must not fetch payloads. Actual task
listing still reconstructs task status; it is not a names-only operation.
Cleanup becomes local to a task. These are distinct benefits, not a claim that
per-task queues make every bulk operation faster.

Do not add a registry service, durable index, writer trimming, migration shim,
startup latch, lease, or new public API. Keep the existing liveness policy and
probe workers. There are two deliberate semantic changes beyond naming:
namespace-based short-ID resolution, and scheduled sampling by LivenessMonitor.
Retirement also gets an explicit older-before-latest rule to avoid exposing a
superseded live snapshot. Their spec deltas are below.

The follow-on [Bounded Status Reconstruction and Monitor Work Plan](2026-09-11-bounded-status-and-monitor-work-plan.md)
removes repeated log replay, eager monitor reads, and quadratic scheduling.
Those changes are not prerequisites for the namespace conversion. The first
plan must nevertheless avoid new all-task reads in point operations and new
namespace work on every reactor turn.

## 2. Baseline, evidence, and cost model

Code/spec baseline: `a1e7d496437545579985f63d2fa130666b64a162`.
The earlier draft's baseline said the worktree was clean at authoring. This
revision was made with unrelated plan/index edits present; they must be preserved.
Read both lineage plans: [landed custody split](2026-08-29-liveness-reaper-and-custody-split-plan.md)
and [superseded namespace proposal](2026-08-27-per-tid-liveness-registry-and-monitor-split-plan.md).
Inherit custody, append-only writes, and shared policy, not writer self-trimming.

Let N be retained task queues, H their total snapshot rows, C selected candidate
TIDs, and L task-log rows. A flat mapping fold reads and parses H rows even for
one known TID. The namespace reads one newest valid snapshot per requested TID.
Malformed newest rows require further reads; there is no constant worst-case
bound when arbitrary malformed history is allowed. Namespace listing avoids
JSON, but the installed SimpleBroker SQLite DISTINCT query can visit historical
index entries. Do not call it O(N) independently of history. Bulk snapshots
require N point reads and can cost more than a shallow flat fold. Superseded valid
rows from frequently publishing tasks now also await the 600-second history
reconcile, rather than being reduced on incremental reactor turns. The age floor
still applies, so history can cover more than 600 seconds; failures/restarts can
extend retention further. Benchmark hot valid publications as well as malformed
tails, including the history reread cost when latest retirement becomes eligible.

Exploratory local measurements used installed SimpleBroker 8.1.1, real SQLite,
roughly 529-byte snapshots, median of three runs, and shared broker scopes.
Both layouts included JSON decoding for state reads. These are evidence, not
portable latency gates or end-to-end status measurements:

| N | Rows/TID | Flat fold ms | Names ms | One TID ms | All snapshots ms |
|---:|---:|---:|---:|---:|---:|
| 1,000 | 1 | 4.565 | 0.261 | 0.024 | 16.570 |
| 1,000 | 10 | 37.992 | 0.554 | 0.022 | 17.572 |
| 1,000 | 100 | 439.007 | 3.028 | 0.032 | 23.885 |
| 10,000 | 1 | 47.946 | 2.758 | 0.030 | 188.550 |
| 10,000 | 10 | 467.045 | 5.125 | 0.037 | 204.467 |

There is no user-imposed 50ms gate and no arbitrary relative admission gate.
Acceptance is correctness plus operation-count/scaling checks. Reproduce a small
benchmark harness during implementation, recording environment, payload size,
rows decoded, queries, and end-to-end timings. Include both shallow and deep
histories; do not hide the bulk-read regression.

## 3. Caller and specification ledger

Paths below are relative to the repository. Read the named owner and its callers,
not just occurrences of the old constant. Re-run the transitive caller sweep at
implementation start; any new caller must get a row and firing test before landing.
All code paths use the same decoder and queue-name construction.

| Owner / callers | Required conversion and preserved behavior | Governing spec |
|---|---|---|
| `weft/_constants.py`; `weft/core/tasks/base.py` registration, payload builder, support queue topology, publication, terminal latch | Per-task queue; unchanged payload/edges; topology validates before DB access; update `waiting_on` strings and internal role consistently | 01-Core_Components [CC-2.2], [CC-2.2.1], [CC-2.4], [CC-2.5]; 07 [QUEUE.7], [OBS.6], [OBS.6a], [LIVENESS.R6] |
| `weft/liveness/policy.py`; new shared readers in `weft/core/task_state.py`; `weft/core/tasks/liveness_monitor.py` | Suffix-bound decoding, scheduled refresh, local custody; no second liveness policy | 01 [CC-2.3]; 05 Cleanup Boundary; 07 [LIVENESS.R1–R10], [OBS.4], [EXEC.3] |
| `weft/core/heartbeat.py`; `weft/core/manager_runtime.py::_lookup_manager_pid`; `weft/core/manager.py::_latest_tid_runtime_handle` | One known TID; keep scoped process identity and creation-time checks | 05 [MF-3.2]; 01 [CC-3.2]; 07 [OBS.4], [EXEC.3] |
| `weft/core/manager.py::_observe_admission_usage` | Bulk current snapshots only when configured; strict failures and same live/generation policy. Disabled limit and PostgreSQL admission paths stay unchanged | 03-Manager_Architecture [MA-1.8]; 07 [MANAGER.18] |
| `weft/core/endpoints.py` latest-mapping helpers, `list_resolved_endpoints`, `resolve_endpoint` | Shared helper, candidate owner TIDs after endpoint matching; retain pure predicates | 05 [MF-3.1]; 07 [LIVENESS.R7] |
| `weft/core/pruning/runtime.py::_streaming_candidates`, `_endpoint_candidates`, `run_runtime_prune_for_context` | Read candidate owner snapshots. Streaming protection requires valid snapshot presence, not merely a queue name. No state deletion by pruning | 05 Cleanup Boundary / Queue Lifecycle; 07 [OBS.16], [OBS.17]; 10-CLI_Interface [CLI-6] |
| `weft/core/monitor/task_monitor.py` runtime-protection helpers | Candidate snapshots for precheckpoint recovery, stale-open summaries, stale service owners, terminal runtime cleanup, reserved cleanup, dead-task cleanup | 07 [OBS.13.3], [OBS.13.4], [OBS.13.6], [OBS.13.7], [OBS.13.10], [OBS.13.11], [OBS.14], [LIVENESS.R7]; 05 Cleanup Boundary |
| `weft/core/monitor/policies/runtime_control.py`, `policies/dead_task.py` | Preserve set-valued pure policy inputs. Reuse existing cheap age/shape eligibility to form candidates; do not put broker callbacks in reducers. Dead-task cleanup must still discover residue without a state queue | Same cleanup contracts; 07 [OBS.15] for manager supersession |
| `weft/commands/system.py::_read_tid_mappings`, `_resolve_tid_filters`, `_collect_task_snapshot_records`, `_collect_internal_service_snapshots` | Names for short IDs; candidate snapshots for filtered collection, bulk for all tasks; reuse a bulk result already obtained in the same collection. Preserve log authority and service projection | 05 [MF-5]; 03 [MA-1.6a]; 10 [CLI-1.2.1], [CLI-1.2.3]; 09-Implementation_Plan [IP-1], [IP-1.0], [IP-1.1] |
| `weft/commands/tasks.py` `resolve_full_tid`, `_command_tid`, `task_tid` | Full-ID direct path; short-ID names and canonical short form; PID lookup still reads snapshots newest-first; reverse full-to-short is arithmetic | 07 [OBS.5], [OBS.6]; 10 [CLI-1.2.3] |
| `weft/commands/tasks.py::mapping_for_tid`, status/snapshot/terminal/ping/pipeline/process helpers | One TID, preserve pipeline precedence, explicit PING, Monitor fallback, and process evidence | 05 [MF-5]; 07 [OBS.10], [OBS.11], [OBS.11a], [OBS.12], [OBS.12a]; 10 [CLI-1.2.3]; 12-Pipeline_Composition_and_UX [PL-5.2], [PL-5.3] |
| `weft/commands/tasks.py::stop_tasks`, `kill_tasks`, `_latest_task_entry`, `_require_controllable_task`, `_ControlSurfaceResources` | Resolve every ID before batch writes; candidate initial reads. Refresh on escalation; preserve fresh/current/initial fallback and control authorization. Resource owner receives exact state queue explicitly, includes it in identity/rebuild decisions, and retains dynamic pipeline/control routes | 10 [CLI-1.3], [CLI-1.2.3]; 07 [OBS.4], [OBS.12a]; 12 [PL-5.3] |
| `weft/commands/_spawn_submission.py` mapping existence, static/dynamic reconciliation queue specs, `reconcile_submitted_spawn` | Valid point snapshot is spawn evidence; names alone are not. Move state subscription from static specs to tid-specific specs, preserve dynamic manager-reserved watchers and mapping/log/queued/reserved/unknown precedence | 10 [CLI-1.1.1]; 05 [MF-1], [MF-6], [MF-7]; 03 [MA-2], [MA-3] |
| `weft/commands/tasks.py::watch_task_status`; `weft/commands/events.py` realtime snapshot producer | Exact state queue plus existing log/control surfaces. Each independent realtime snapshot remains fresh | 09 [IP-1.1]; 14-Python_API_Surfaces [PY-2]; 05 [MF-5] |
| `weft/commands/system.py::_iter_public_status_events`; events `iter_task_events`, `follow_task_events`; CLI status/task-status `--watch` | Existing event cursors remain event streams; do not replace them with snapshot polling | 10 [CLI-1.2.1], [CLI-1.2.3]; 14 [PY-2] |
| `weft/client/_namespaces.py`, `_task.py`; commands result / `_result_wait` / `_streaming` through task evidence | Public adapter parity and result waiting; no signature changes. Python system status remains unfiltered while CLI defaults hide terminal tasks | 09 [IP-1.0], [IP-1.1]; 14 [PY-1], [PY-2]; 10 [CLI-1.2.2] |

Cross-cutting specs: `04-SimpleBroker_Integration.md` [SB-0.4] owns backend/context
boundaries; `02-TaskSpec.md` owns payload/runtime identity fields;
`08-Testing_Strategy.md` [TS-0], [TS-3.1] requires real queue fixtures.
`00-Overview_and_Architecture.md` preserves queues as truth. No Weft SQL shortcut
or second durable status store is allowed. `11-CLI_Architecture_Crosswalk.md`
must retain the command ownership mapping; this plan moves no public owner.

## 4. Reader and custodian design

Add `weft/core/task_state.py` for shared broker readers, using existing broker
context APIs. Keep decoding/reduction in `weft/liveness/policy.py` and the
`weft/liveness/` evidence facade broker-free. Do not relax architecture tests or
re-export broker readers through that facade. It owns `task_state_queue_name(tid)`, names-only
`list_task_state_tids(ctx)`, `read_task_state_snapshot(ctx, tid)`, and bulk
`latest_task_state_rows(ctx, tids=None, ...)`. Broker acquisition and read
failures propagate; existing caller-level error policies remain responsible for
recovery. Do not globally convert errors to empty state or retain a no-op mode
flag. Candidate bulk reads deduplicate TIDs and return immediately for
an empty set. One broker scope serves a bulk operation. Helpers share the decoder
and do not acquire one connection per TID.

Match the existing full-TID syntax check in `resolve_full_tid`: exactly 19 ASCII
decimal characters. Use `weft.helpers.tid_short_form` for short forms. Keep the
full-TID shape check in `weft.helpers.message_ids.is_task_tid`, shared by
queue naming, point/bulk reads, short-form formatting, and command validation.
The existing exact-message-ID normalizer shares that shape check and delegates
range validation to SimpleBroker. TaskSpec retains its nonzero requirement.
Do not import command-layer resolution into core. Invalid point selectors are
absent without I/O; name construction remains strict. A snapshot is valid only if its existing
required fields are valid and its `full` equals the queue suffix. Do not widen
payload validation or infer a live handle from a bare PID.

A reader peeks newest first with limit one. If malformed, continue newest-first
using the public `before_timestamp` pagination until the first valid snapshot
or exhaustion. A fixed malformed-row ceiling would hide valid evidence, so it is
not allowed. Readers never delete. Names-only discovery includes a syntactically
valid nonempty queue even if all its rows are malformed. Short-ID collisions in
that namespace fail deterministically; all other evidence consumers require a
valid snapshot. Empty virtual queues disappear naturally.

LivenessMonitor performs one full namespace snapshot refresh every five seconds,
using `LIVENESS_STATE_REFRESH_INTERVAL_SECONDS = LIVENESS_PROBE_INTERVAL_SECONDS` in `_constants.py`.
The namespace prefix is `WEFT_TASK_STATE_QUEUE_PREFIX = "weft.state.tasks."`;
remove `WEFT_TID_MAPPINGS_QUEUE` after the atomic caller migration. The fast reactor still handles
controls and completed probes between refreshes; it does not enumerate the
namespace every 50ms turn. First startup performs full reconciliation. Schedule
next refresh/reconciliation from completion to coalesce overdue work. Preserve
the existing 600-second history reconciliation and five-second probe cadence.
Refresh work can delay observation beyond five seconds; there is no hard SLA.
Known-TID API/CLI reads do not wait for this refresh.

Compare message IDs before repeated decode/generation computation when possible.
Refresh samples the latest valid generation: unseen A→B→A changes coalesce and
do not reset the deadline of already observed A. Deadline semantics are relative
to observed generations, not every intermediate publication. Custodian refresh, full reconciliation, and retirement use strict reads. Failed
enumeration must not mean disappearance; a per-queue read failure preserves that
TID's cached evidence and cannot authorize retirement. Successful absence removes
retained state/due work;
an existing in-flight worker remains owned until its result is handled. Preserve
token, message-ID, and runtime-generation checks against late results.

Do not use BaseTask's permanently cached `_queue()` for each discovered TID.
Use short-lived queue facades in a shared broker scope. Retained monitor memory
is O(retained TIDs + bounded in-flight work), not O(all tasks ever seen).

Keep the current 2,400-second age fences for malformed, superseded, and latest
rows. Nothing is deleted “on sight.” Full reconciliation reduces each task's
history and then closes the iterator before exact-ID deletes. Ordinary refresh
reads current state only. Failed historical deletes retry at reconciliation;
failed latest retirement retries through the existing probe schedule.

For eligible latest retirement, reread that task's history before deleting.
Require the reread newest-valid message ID to equal the probed message ID. If
it differs, adopt the observed current row and reject the stale result, even if
the observed row is older. If no valid row remains, discard that retirement work
without deleting unprobed rows. On an exact match, remove eligible observed older
rows before the eligible probed latest row. An already absent older ID counts as
success after verification; a false delete return alone is not a failure proof.
If an older valid row remains or absence cannot be verified, retain the latest
and retry: deleting
it first could expose an old nonterminal snapshot as current. Younger malformed
residue may remain. Delete only observed IDs, never the queue; concurrent unseen
appends survive. No lease or tombstone is required. Preserve current UNKNOWN
probation, scoped identity checks, restart behavior, and worker cap.

## 5. Proposed spec delta and promotion ownership

The implementation owner makes these in-file edits before code conformance is
claimed, adds this plan's backlink at every changed spec section, and synchronizes
nearby implementation mappings/docstrings. Re-read baseline sections first.

| File and section | Exact intended change |
|---|---|
| 07 [OBS.6], [OBS.6a] | Replace flat storage with per-TID append-only snapshots; suffix-bound newest-valid decoding; names enumerate retained entries, not live processes |
| 07 [OBS.5] and 10 [CLI-1.2.3] | Keep canonical short form; change short-ID candidate membership from valid payload rows to valid namespace names, including malformed-only entries; preserve ambiguity errors |
| 07 [QUEUE.7]; 01 [CC-2.2.1] | Support state queue is task-specific and validated before queue wiring |
| 07 [LIVENESS.R3], [LIVENESS.R6], [LIVENESS.R7] | Custody/append-only rules apply to the namespace; protection uses newest valid snapshot presence. Operator queue deletion remains an explicit override. Document older-before-latest retirement and failed-delete preservation |
| 07 [LIVENESS.R4], [LIVENESS.R8], [LIVENESS.R10]; 01 [CC-2.3] | Latest-generation sampling every five seconds, separate from reactor turns; sampled generation/deadline semantics; no fixed one-peek guarantee for malformed history; bounded retained handles/state |
| 01 [CC-2.2], [CC-2.4], [CC-2.5]; 00 Quick Reference queue table/notes | Rename publication target, preserve fields and edges; concise namespace note and historical rename lineage |
| 03 [MA-1.8] | Replace full flat-history folding description with bulk current snapshots; preserve admission accounting and backend-specific behavior |
| 05 [MF-3.1], [MF-3.2], [MF-5], [MF-6], Cleanup Boundary, Queue Lifecycle | Update read/watch/custody owners and state names; add `weft/core/task_state.py` as broker-read owner to 07 implementation mapping and preserve broker-free evidence ownership; separate names, valid evidence, and process liveness; describe tid-specific spawn subscription |
| 10 [CLI-1.1.1], [CLI-1.2.1], [CLI-1.2.3], [CLI-1.3]; 09 [IP-1.1]; 14 [PY-2] | Synchronize affected observation descriptions and implementation mappings; no new command, signature, result shape, or event-watch semantics |

All other ledger contracts are preservation obligations, not permission to
rewrite unrelated specs. Sweep `docs/specifications/`, `README.md`, `AGENTS.md`,
`CLAUDE.md`, and `docs/agent-context/runbooks/` for the old name. Preserve lineage,
CHANGELOG cutover commands, lessons, and historical plans. Record every remaining
production/test occurrence with a reason; a simple constant search is insufficient.

## 6. Implementation slices and firing tests

1. **Promote the scoped spec deltas and pin contracts.** Update names/topology,
   short-ID membership, monitor sampling, and retirement specs. Add real-broker
   tests for readers, suffix mismatch, malformed tails, age gates, and late probe
   results. Record operation counters before conversion. This is a proposed-spec
   slice until its matching implementation lands, not a claim of completion.
2. **Convert producers and every consumer atomically.** Implement the shared
   readers, publication, custody, topology, all ledger callers, and the harness.
   Review this in smaller diffs if useful, but do not land a runnable intermediate
   state that removes the constant while consumers still import it or splits
   writers/readers across layouts. Candidate cleanup discovery must not truncate
   protected candidates before applying policy and thereby starve later work.
3. **Verify behavior, scaling, and cutover.** Exercise the matrix below, run
   scoped suites then repository required checks, reproduce the benchmark, and
   review the complete caller/spec sweep independently. Update usage docs and
   CHANGELOG with the stop-all upgrade and rollback instructions.

Test ownership includes existing files (rename test names only when useful):
`tests/helpers/weft_harness.py`, `tests/test_harness_registration.py`,
`tests/architecture/test_liveness_boundaries.py`,
`tests/tasks/test_liveness_monitor.py`, `test_runtime_identity_custody.py`,
`test_heartbeat.py`, `test_task_observability.py`, `test_task_endpoints.py`,
`test_runtime_identity_signals.py`, `test_command_runner_parity.py`,
`test_task_monitor.py`, `test_task_execution.py`, `test_pipeline_runtime.py`;
`tests/core/test_client.py`, `test_manager.py`, `test_heartbeat_helpers.py`;
`tests/commands/test_tid_mapping_contracts.py`, `test_task_evidence.py`,
`test_task_commands.py`, `test_status.py`, `test_run.py`,
`test_manager_commands.py`, `test_runtime_prune.py`, `test_realtime_events.py`;
`tests/system/test_short_tid.py`, `tests/specs/quick_reference/test_queue_names.py`;
`tests/cli/test_cli_system.py`, `test_cli_manager.py`, `test_status.py`,
`test_cli_run.py`. Resolve shorthand filenames within the preceding directory.

| Contract | Required firing case |
|---|---|
| Point vs names vs bulk | Unrelated task history grows: point reads/decode do not; names decode zero payloads; C candidates read only C queues; empty candidates do no state I/O |
| Validation | Empty namespace, malformed suffix, mismatched full ID, malformed newest and multiple malformed pages hiding an older valid row; canonical short collisions including malformed-only queue |
| Custody | All 2,400s fences; exact observed IDs; append during retirement survives; missing probed row rejects result; already-absent older IDs succeed; failed older deletion preserves latest; late generation/probe ignored; malformed residue does not authorize control |
| Refresh | Fake clock: many reactor turns cause no namespace refresh until due; new entry discovered on refresh; A→B→A sampling; enumeration/per-TID read failure vs true disappearance; restart and in-flight cap |
| Retained resources | Repeated create/retire cycles do not retain every historical queue facade or due entry |
| Adapters | Full/short/PID/reverse; status plain/JSON/filter/all/stats; pipeline/ping/process; client snapshot/watch; CLI event watch; result wait; batch control resolves before writes and escalation refreshes |
| Spawn/control waits | Subscribe before the task-state queue exists, then verify its first write wakes the waiter; open the lazy Queue handle even when enumeration finds no name. Also test ordinary exact-queue wakeup; dynamic manager reserved and pipeline route rebuild; valid mapping evidence and unknown fallback; subscription closes on exit |
| Cleanup/admission | Candidate policy parity; missing-state residue still found; strict admission read failure; disabled and PostgreSQL branches; services and terminal proof retain precedence |
| Dump/topology | Runtime namespace excluded from dump; topology validation before DB; harness detects registration and cleans up |

Use `. ./.envrc` and repo-managed pytest, mypy and ruff. Run affected suites first,
then the project's required full verification before implementation completion.
Tests must assert observable behavior and real broker work, not merely mirror
helper internals. The draft itself needs plan metadata/spec hygiene and diff checks.

## 7. Rollout and rollback

This is a downtime conversion, not a rolling upgrade. For each broker context,
stop every task and service, including managers and both monitors, before changing
code. Verify their processes have exited. Install the new code, explicitly delete
`weft.state.tid_mappings` with `weft queue delete weft.state.tid_mappings` in that
context, then restart services/tasks. Verify new publication, full/short status,
spawn reconciliation, and custodian retirement. PING does not publish state and
is not a repopulation mechanism. There is no mixed-version support.

A missed old process recreates legacy state and has no namespace-based protection
or admission visibility. Stop/restart it and remove the legacy residue. Do not
paper over this with a startup latch. For rollback, stop all processes again,
restore the previous code, explicitly remove the new runtime namespace entries
using normal queue tools, and restart to rebuild flat state. Never delete the
durable task log as part of either direction. Operator verification owns cutover;
normal Weft components retain sole-custodian state deletion rules.

## 8. Review, risks, and revision log

The principal accepted costs are N point reads for bulk status/admission, at least
one namespace sweep per refresh, and delayed sampled monitor observation. A bad
malformed tail can still require reading history. Cold task status still replays
the global task log; this plan does not claim to fix that. The follow-on removes
avoidable repeated work without creating another source of truth.

Independent review must cover the entire caller ledger, every changed spec clause,
and the actual diff; classify findings by correctness or removable complexity.
Resolve blocking findings before implementation. Preserve review history below,
but its original “no semantics changed” and every-turn bounded-peek conclusions
are superseded by this revision. This draft is not yet an implementation approval.

### Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|
| OBS.6 / CLI-1.2.3 | Namespace membership for short resolution | Current code reads valid flat payloads | Avoid JSON for discovery while preserving explicit ambiguity | §5 |
| LIVENESS.R4 / R10 | Scheduled sampled refresh | Current flat incremental ingestion observes intermediate rows | Avoid adding N queries to each reactor turn | §4–5 |

### 2026-09-11 revision

User requested correctness and simplification after a deep caller/cost review.
Removed the invented 50ms gate, “on sight” trimming, and blanket semantic-parity
claim. Added the missing spawn/control subscriptions, candidate read scopes,
explicit sampling semantics, and local retirement ordering. Original review
records follow unchanged as historical evidence, not current acceptance claims.

## 10. Fresh-Eyes Review

Author self-review: completed 2026-09-11, separate pass after drafting.
Findings, fixed in place:

1. Task 3 kept a `WEFT_TID_MAPPINGS_LEGACY_QUEUE` constant "for the
   operator delete," but the operator delete is a documented CLI command
   using the literal name — no code reads a constant. Resolved: the old
   constant is removed entirely.
2. The cutover command's existence was asserted, not verified. Resolved:
   `weft queue delete` confirmed against
   `weft/commands/queue.py::cmd_queue_delete`.

Independent review: completed 2026-09-11, one scoped round per §8.
Dispositions below; no further rounds (house rule).

## Independent Review Record

Append-only. Fourteen findings; all adopted (each either fixed a
correctness defect or removed ceremony — none was rebutted).

| # | Severity | Finding | Disposition |
|---|----------|---------|-------------|
| 1 | blocking | Cutover premise false across versions: old-code tasks carry the old queue name baked in, so "republish on next edge" resurrects the deleted legacy queue and never repopulates the namespace; rollback has the mirror defect | **Adopted.** Stop-every-task is now mandatory in both directions (§1, §4 Rollback, Task 6); missed-task consequences documented (orphan legacy rows → second operator delete; no R7 protection or admission visibility until restart). Matches the plan owner's original bring-down/bring-up framing. |
| 2 | blocking | `weft task ping` publishes no snapshot (PING handler fires no mapping edge), so the ping-sweep mitigation was fiction | **Adopted.** Mitigation deleted; no code change added — the mandatory stop step is the control. |
| 3 | should-fix | Task 7 grep gate required a legacy constant Task 3 removes — unsatisfiable | **Adopted.** Gate now expects only lineage notes and the CHANGELOG entry. |
| 4 | should-fix | Amended R3 forbade the whole-queue deletes the plan's own rollback and tests perform | **Adopted.** Scoped to Weft components; operator CLI deletes are a human override, safe by wipe-rebuild. |
| 5 | should-fix | "Newest valid row (one bounded peek)" under-specified: a malformed newest row could mask the valid snapshot and silently drop R7 protection | **Adopted.** [OBS.6] delta now states the read rule (newest-first, skip malformed, first valid row); Task 4 adds masking tests for singular and bulk reads. |
| 6 | should-fix | R10 "per retained TID" deferred newly appeared queues to the next full reconcile | **Adopted.** "Per enumerated queue," with first-listing read/probe entry stated. |
| 7 | should-fix | Inventory missed the bulk read model (`endpoints.py::latest_tid_mapping_rows`) most readers actually use | **Adopted.** Bulk helper `latest_task_state_rows` named; endpoints implementation moves/delegates; per-site composition forbidden (§4, Task 4). |
| 8 | should-fix | [QUEUE.7] topology validation pins the flat name — every task fails construction under the naive writer edit | **Adopted.** Added to Hidden couplings and Task 3, with the `waiting_on` string sweep. |
| 9 | should-fix | Rename sweep missed README.md, AGENTS.md, CLAUDE.md, and a runbook | **Adopted.** Sweep scope extended in the delta and Task 7. |
| 10 | consider | Literal sweep would rewrite lineage notes and the CHANGELOG command | **Adopted.** Exclusions stated. |
| 11 | consider | Backlinks only in 05/07 despite four touched specs | **Adopted.** All four in Task 2. |
| 12 | consider | Admission undercount during the missed-task window unnamed | **Adopted.** Folded into Task 6's consequence paragraph. |
| 13 | consider | "Trim cadence" overstated the bound (newest-peek cycles skip intermediate rows until reconcile) | **Adopted.** Compass now says full-reconcile interval and why. |
| 14 | removable-ceremony | Quick Reference note duplicated the Compass essay | **Adopted.** Note cut to one sentence plus pointers and lineage. |

Reviewer's clean checks, recorded: no landed [LIVENESS.R1–R9] semantic
reversed; strategy-A slice order compliant; exact-ID deletion maps onto
the existing custodian delete path; the per-TID watermark is the existing
`_latest_rows` map so R10's memory claim holds; dump exclusion tested not
assumed; phantom-name and short-form inheritances carried; the
deliberately-not-built list "all correct removals."


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

Policy/layering review correction: adopted `weft/core/task_state.py` as the shared
broker-reader home. The earlier under-liveness placement contradicted the
broker-free package contract even though the fixed-module architecture test would
not catch a newly added reader module. No test boundary is weakened.

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


### Implementation execution, 2026-09-11

Class 5, user-authorized namespace implementation. Outcomes: promote reviewed
spec deltas; migrate producers/readers/watchers/custodian atomically; preserve
payloads, identity authority, age fences and lifecycle evidence; run real-broker
contract/scaling tests and independent review. Follow-on plan remains out of scope.
Spec promotion baseline: `a1e7d496` plus uncommitted namespace diffs in specs
00/01/03/05/07/09/10/14. Prior unrelated result-plan and index edits are preserved.

Failing-first evidence: new monitor discovery test failed because the old monitor
ignored a per-TID queue; command namespace resolution/spawn tests failed on the
old flat readers; the shared-reader suite initially failed import before its new
owner existed. Production readers now live in `weft/core/task_state.py`; existing
private publisher/builder names remain unchanged to avoid a needless method rename.


Implementation cost check: an exploratory real-broker harness (removed after
measurement at the user's request) built a snapshot through a real Consumer and
measured equal valid publication histories. Counters printed as `expected_*` are the all-valid fixture
model; actual point/bulk operation counts are asserted by command regression tests.
Local SQLite, 514-byte payload, median of three, shared scope, JSON included:

| Tasks | Rows/task | Flat fold ms | Names ms | Point ms | Unchanged ms | Bulk ms | Local retirement history ms |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1,000 | 10 | 37.943 | 0.615 | 0.029 | 0.019 | 21.877 | 0.062 |
| 10,000 | 1 | 42.262 | 3.186 | 0.040 | 0.019 | 206.959 | 0.029 |
| 10,000 | 10 | 453.876 | 5.957 | 0.037 | 0.019 | 219.683 | 0.050 |

The shallow-history bulk regression remains explicit. No latency acceptance gate
was added. An exact raw message-ID match lets the monitor reuse its existing
validated payload; its regression failed on the missing parameter before the
implementation passed. This is reuse of retained monitor state, not another cache.

Integrated review dispositions: malformed bulk candidate owner IDs are filtered
centrally (preserving absent-evidence semantics); point names remain strict.
Stale-service decisions reuse their already-read service rows. Harness failure
injection now tests broker-scope release rather than assuming physical Queue
closure; the broker owns connection pooling. Public APIs and follow-on work remain
unchanged.


### Implementation verification and review handoff

- Clean integrated default-suite rerun: `python -m pytest --tb=short`:
  **4,788 passed, 14 skipped, 5 failed**, 291.73s. All five failures are unchanged
  dependency-floor assertions in `tests/system/test_optional_extras.py`:
  SimpleBroker 8.1.1 versus expected 8.0.0; simplebroker-pg 4.1.1 versus expected
  4.0.0 in pg/all/dev; llm 0.35 versus expected 0.33. An independent reviewer
  reproduced the same five failures from exact HEAD `a1e7d496` files in an isolated
  tree. Dependencies and unrelated assertions were not changed.
- `python bin/pytest-pg -- tests/core/test_task_state.py
  tests/tasks/test_liveness_monitor.py tests/commands/test_tid_mapping_contracts.py
  tests/commands/test_runtime_prune.py -n 4 -q`: **121 passed** against a real
  wrapper-provisioned PostgreSQL backend. Wrapper exit code 0.
- Full project mypy: **189 source files clean**. `ruff check .` and
  `git diff --check`: clean. Plan/spec hygiene, audit inventory, and suppression
  policy suites: **59 passed**. The new core test module is in the shared inventory.
- Final old-name sweep across production, tests, integrations, extensions, bin,
  specs, README, AGENTS and runbooks leaves only the deliberate README cutover
  command and Quick Reference lineage. No runtime compatibility constant remains.
- Independent command/custodian/caller reviews found no remaining namespace
  correctness blockers after the recorded fixes. A different-family source/tool
  review timed out after 600 seconds without a verdict; a bounded supplied-diff
  retry also timed out after 240 seconds. Neither is counted as approval. The
  completed independent reviews above provide the review evidence; different-family
  review remains an explicit limitation.
- Backstitch was unavailable in PATH, repository toolchain, and configured tools;
  no backstitch success is claimed. Available spec/plan/audit gates above passed.

The reproducible benchmark additionally measures a real single-task Python
`task_snapshot` call (including collection/projection): 7.995ms in a separate
one-task producer context. This is not a claim about N-task lifecycle-log scaling;
the follow-on still owns repeated lifecycle reconstruction.

All implementation/spec/test changes are left uncommitted for review. The plan
keeps draft status pending landing; no production broker was migrated or cleared.
Rollout requires the documented stop-all cutover. The separate bounded-status and
monitor-work plan remains unimplemented.

### Test-suite follow-up (2026-09-11)

Class 2: remove brittle dependency-version assertions at the user's direction.
The outcome is removal of the three exact-floor tests (five cases) in
`tests/system/test_optional_extras.py`; dependency declarations, runtime behavior,
extra consistency and dependency-ownership checks remain unchanged. Commit
`453f54ed` had intentionally raised the manifest floors without updating these
assertions. Updating the expected versions, or replacing equality with minimum
checks, was rejected in favor of deleting the duplicate version policy.

Failing-first check reproduced the five assertions. The full default suite begun
before deletion completed with 4,788 passed, 14 skipped and exactly those five
failures (305.89s); it had collected their old definitions. After deletion,
`python -m pytest tests/system/test_optional_extras.py -n 0 -q` passed all seven
remaining cases. Focused Ruff lint/format and `git diff --check` passed. No other
failures were observed. This is not a claim of a fresh full-suite run after
deletion. Changes remain uncommitted.

### User defect audit correction (2026-09-11, in progress)

This correction inherits Class 5: shared identity/read-error contracts and
custodian deletion proof. Baseline is HEAD plus the existing namespace worktree;
concurrent test typing, toolchain, and CI changes are outside this correction.
Outcomes: centralize full-TID spelling validation under existing
`weft/helpers/message_ids.py`; invalid point selectors return absent without I/O;
verify exact-ID absence before successful retirement; remove obsolete wrappers,
dead fallback, and duplicate predicates; preserve cleanup counters; alias refresh
to the probe interval; remove the exploratory benchmark script at user request.
TaskSpec's exact-ID range and nonzero requirement, short selector resolution,
liveness policy, and caller-level failure handling must not change.
Verification: failing-first point/manager/retirement/error-boundary regressions,
focused tests, full default suite, live PostgreSQL reader/retirement tests, lint
and type checks. Independent agents review and repair bounded slices.

Error-boundary delta independently reviewed before promotion: ordinary broker
acquisition/read errors propagate, preserving real baseline behavior. The former
flat helper's generator-construction catch never covered real lazy acquisition;
the namespace conversion had incorrectly moved it. Removed the obsolete strict
flag instead of preserving a meaningless mode. New tests pin acquisition, list,
first read, and pagination failures.

The exploratory benchmark script was removed at user request; measurements above
remain historical evidence. Duplicate full-TID predicates now share the existing
message-ID helper module. Short-selector parsing and nonzero/range checks remain
explicit distinct constraints. The control resource owner omits a task-state
subscription for non-task IDs while preserving existing log/control observation.
No obsolete all-rows endpoint wrapper remains. Small payload projection adapters
remain because they have distinct callers and share the actual reader/decoder.

Audit verification and independent review:

- Invalid point-read regression failed with ValueError before repair. Public
  manager-force-stop tests now pin ControlRejected; child-launch recovery tests
  prove log evidence is still visited for non-task selectors. Full-TID spelling
  has one predicate; submission uses the existing exact-message-ID normalizer.
- Three retirement fault cases failed before repair: older row retained after
  a reported deletion of zero or one, and an absence-read failure. Exact peeks
  include claimed rows; latest deletion requires observed older-row absence.
- Broker acquisition failure regression failed before removing the new swallow.
  Acquisition/list/first-read/pagination errors are now pinned at the shared owner.
- Counter regressions cover both young and retention-deferred live data-only
  queue families; selection remains unchanged while skipped-live classification
  is restored. State reads stay scoped to discovered residue; the duplicated
  initial actionable-selection pass was removed.
- Independent identity/retirement review found no blockers (53 helper/reader/
  manager/monitor cases and 112 TaskSpec/submission cases passed). Separate
  error-boundary and cleanup reviewers found no remaining blockers.
- Real PostgreSQL wrapper rerun of reader, monitor, TID contracts, and runtime
  pruning: 132 passed, exit0. The first run collected a subsequently corrected
  test that incorrectly expected the unchanged private control-queue API to
  accept Unicode queue names. Public validation and absent state are tested
  separately from that existing broker queue-name restriction.
- Focused corrections: 264 cases passed, followed by nine manager/submission
  regressions, 91 reader/prune/spec cases, and 25 TID contract cases. Spec/plan
  hygiene: six passed. Project mypy including tests: 418 files clean.
- First full default run: 4,783 passed, 14 skipped, 11 failed. Independent
  provenance review traced ten to concurrent test-typing edits, and the remaining
  thread-timing failure passed in isolation with unchanged production code.
  Those behavior failures subsequently passed after the concurrent repairs.
  A fresh combined full-suite run is pending; no full-green claim is made here.

Remaining digit checks were audited: result selectors, unresolved short selectors,
service-record numeric fields, and age/log-window parsing have broader contracts
than full-TID spelling. They are not alternate implementations of the canonical
full-ID validator. Formatting exact message IDs remains delegated to SimpleBroker.

Final correction verification (current worktree, uncommitted):

- Fresh full default suite: 4,766 passed, 14 skipped, four failed in 425.82s.
  Two policy tests observed an in-flight B023 issue in concurrent architecture
  test edits; a nested-pytest diagnostic and pipeline restart timed out while
  other suites were running. All four exact cases reran together with `-n 0`:
  four passed in 38.73s. This is reported as a full run plus successful focused
  reruns, not as a clean single full-suite pass. The changing test count reflects
  concurrent test-audit edits, not namespace test removal.
- Final mypy: 418 source files clean. Whole-tree Ruff clean. TID contracts:
  25 passed. Spec/plan/suppression-policy gates: 49 passed. Formatter and
  `git diff --check` clean. PostgreSQL focused wrapper: 132 passed.
- All four user defects and requested benchmark removal are addressed. No
  namespace blocker remains from independent reviews. The follow-on scanner
  failure diagnosis was corrected to preserving existing propagation, rather
  than claiming a nonexistent ordinary backend-error swallowing defect.
- No commit, production broker migration, or cleanup was performed. Concurrent
  test-typing/toolchain/CI edits were preserved and their owners repaired their
  own failures during verification.

### Closure (2026-09-11)

Closed at the user's request after implementation, defect correction, independent
review, and the verification recorded above. The bounded-status follow-on remains
a draft and is not implemented. This commit contains namespace work and its
regressions; unrelated concurrent test-typing, toolchain, and CI changes are excluded.
Operational deployment still requires the documented stop-all cutover; closure
does not claim that a user broker has been migrated.

The exact staged contents were also tested in an isolated worktree, excluding
concurrent edits. Full suite: 4,805 passed, 14 skipped, three failed in 315.96s.
Two observability tests timed out under parallel load and both passed on immediate
serial rerun (0.92s). The remaining metadata failure is present in the parent
commit: the unrelated persistent-result plan links to a missing superseding
plan. Its concurrent correction is intentionally excluded. This is not a clean
single full-suite pass. Isolated Ruff passed and production mypy passed for
189 source files; the focused namespace/spec run passed 140 tests with only that
same baseline metadata failure. Staged whitespace checks passed.
