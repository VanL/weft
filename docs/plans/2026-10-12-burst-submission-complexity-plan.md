# Burst Submission: Local Completion and Incremental Manager Bookkeeping

Status: completed
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-2.1], [MF-3], [MF-3.1], [MF-5]; docs/specifications/03-Manager_Architecture.md [MA-1.6], [MA-1.6a], [MA-2], [MA-3]; docs/specifications/07-System_Invariants.md [OBS.1], [OBS.6], [OBS.13.9], [IMPL.10]; docs/specifications/01-Core_Components.md [CC-2.3]
Superseded by: none

Class: 5. Planning began at Class 5 because removing global lifecycle observation changes a governing protocol and its cleanup lifetime. Runtime execution, supervision, and failure detection make the implementation risky.

## Goal and motivation

Remove repeated work proportional to unrelated pending submissions from ordinary submission/result observation and manager launch bookkeeping. Start with shared-log fan-out, child-sentinel membership rebuilds, and autostart backlog scans. Preserve task failure, wrapper death, restart recovery, reservation disposition, and completion/output ordering. Admission waiting behind a configured connection/resource limit is outside this change.

The motivation is **weftness**: queues carry durable facts; tasks own their control and result surfaces; the Manager owns coordination; SimpleBroker owns transactions and delivery; Python's process primitives report child exit. A completion fact should be addressed to its observers, not rediscovered by every observer searching everyone else's history. An owner that already knows an object's identity should retain that identity and confirm it against the queue, rather than search again. In-memory state is a hint or an owned process handle, never a competing lifecycle database. These are applications of the existing theory of the program, not a new scheduler, event bus, index, or cache subsystem.

This does not promise literal O(1) process creation or arbitrary historical discovery. CPython process startup, standard sentinel waiting, cold recovery, and deliberately requested global event observation have real costs. The target is to remove avoidable cross-products from the ordinary burst path and state the remaining costs precisely.

## Source documents and baseline

Plan type: implementation with spec revision. The user authorized implementation on 2026-10-12. The implementation and promoted specs have passed the verification recorded below; this plan remains non-normative. The promoted specs define behavior.

- [Message flow](../specifications/05-Message_Flow_and_State.md): [MF-1] spawn acceptance, [MF-3] control/terminal wire protocol, [MF-3.1] endpoint evidence, [MF-5] result observation and cleanup.
- [Manager architecture](../specifications/03-Manager_Architecture.md): [MA-1.6]/[MA-1.6a] singleton supervision and sentinel ownership, [MA-2]/[MA-3] assigned identity and startup.
- [System invariants](../specifications/07-System_Invariants.md): [OBS.1–3], [OBS.6]/[OBS.6a], [OBS.13], [IMPL.10]. Also read [Core components](../specifications/01-Core_Components.md) [CC-2.2.1]/[CC-2.4] and [SimpleBroker integration](../specifications/04-SimpleBroker_Integration.md) [SB-0.4].
- [Result observation simplification](2026-10-10-result-observation-simplification-plan.md) records the canonical-history/unified-settlement design now present in the baseline. Preserve its useful simplification. Its metadata is not evidence that its code is absent or that its historical tasks remain a backlog.
- Required agent reading: [README](../agent-context/README.md), [decision hierarchy](../agent-context/decision-hierarchy.md), [principles](../agent-context/principles.md), [engineering principles](../agent-context/engineering-principles.md), [runtime patterns](../agent-context/runbooks/runtime-and-context-patterns.md), [testing patterns](../agent-context/runbooks/testing-patterns.md), [writing plans](../agent-context/runbooks/writing-plans.md), [hardening plans](../agent-context/runbooks/hardening-plans.md), [review loops](../agent-context/runbooks/review-loops-and-agent-bootstrap.md), [agent lessons](../agent-context/lessons.md), and [project lessons](../lessons.md), in the canonical read order.

## Spec Baseline

- `a0d275b44f5b620bbf685386077cee35e065b33d`: the source specifications and implementation inspected for this plan.
- Implementation starts from `c573926ad0442f79f4712879e7693d416b3fd52f`; the retained plan-review refinements and Core Components navigation backlink were the pre-existing local delta. Promotion uses this diff base plus each owning spec/code/test delta, with final evidence recorded below.
- Installed dependency floors inspected: SimpleBroker 8.5.1 and simplebroker-pg 4.5.1. Read the installed implementations or the matching sibling source before making broker performance claims.
- Record the promotion baseline after each spec/code slice, using its commit SHA or the original diff base plus the exact spec diff when review is uncommitted. Do not invent a promotion SHA.

## Evidence and scope

| Finding | Current owner and cost | Planned disposition |
| --- | --- | --- |
| Shared completion history | `_result_wait._acquire_result_observation` scans each new global suffix for every waiter. Public substring filtering saves decoded bodies, not backend scanning. W waiters and E shared events can cause O(W E) search work. | Slice 1 addresses completion facts to configured `ctrl_out` and removes global interest from ordinary configured waits. |
| Child sentinel membership | `_commit_child_launch_success` republishes all children; the adapter deduplicates and filters full snapshots. N launches visit N(N+1)/2 members in the reactor. | Slice 2 makes registration/removal incremental, retaining standard Python waiting. |
| Autostart pending proof | `_pending_service_keys` walks public/reserved spawn backlogs even when a tracked service is live or the same request remains pending. | Slice 3 uses live ownership and exact request references first, with durable cold recovery. |
| Streaming registration | `_begin_streaming_session` searches the registry for the message it just wrote. | Slice 4 retains `Queue.write()`'s returned ID. |
| Manager info summaries | `_child_summaries_for_log` sorts all children before rate limiting, although only a fixed small sample is emitted. | Slice 4 uses `heapq.nsmallest` for the existing lowest-TID sample, preserving current state-change logging. |
| Abnormal submission reconciliation | `_spawn_submission._inspect_task_log_for_tid` replays the suffix from TID on every wake. | Slice 4 retains its inspected horizon and folded outcome during one observation. |
| Endpoint status lookup | `endpoints._latest_task_statuses` scans global history even for one requested name. | Slice 5 limits decoded history to candidate TIDs using existing evidence helpers; report remaining backend substring-search cost honestly. |
| Other manager/registry work | Child cleanup/deadline/user-work scans, service-registry validation, SQLite admission snapshots, and CPython's own child cleanup remain. | Measure separately in the final burst exercise. Keep crash sweeps and schema checks; do not add heaps/counters/registries merely to claim O(1). Admission policy remains unchanged. |

The original spawn enqueue is already one ordinary broker write, not a pending-queue traversal. Do not redesign acceptance or move broker concerns into Weft.

## Invariants, boundaries, and tradeoffs

1. TID assignment/immutability, resolved `spec`/`io` immutability, forward-only task state, spawn context, and keep/clear/requeue policies remain unchanged. This plan concerns resolved runtime work, not stored template mutation.
2. Retain the global lifecycle log and its existing status/Monitor consumers. Runtime mappings remain best-effort configuration/liveness observations, never terminal authority. Do not extend LivenessMonitor into a verdict writer.
3. Task-owned terminal proof wins over Manager `wrapper_lost`, regardless of arrival order. Persistent batch completion is not task termination. Pipeline/edge success and failure must enter the same BaseTask publication path as other tasks.
4. Observe before checking contents; notifications remain hints. Keep `QueueChangeMonitor`, cancellation wake handling, output grace, final nonblocking drain, and `_CollectedResult` claim/settlement ownership. Do not create a second result state machine.
5. Local completion evidence must survive normal task exit and TaskMonitor cleanup while results are retained. Shared/custom control queues retain their existing external ownership. Do not delete a shared/custom queue based on one TID.
6. Keep one sentinel observer, its existing lock/pipe/failure path, and reactor-owned child reaping. Keep PID fallback, terminal-proof grace, silent launch-worker recovery, and bounded shutdown. Do not replace `multiprocessing.connection.wait` with a platform-specific selector or one thread per child.
7. Service request references are confirmed hints in `ManagedServiceState`. Missing/error is not automatically absence. Preserve trusted metadata parsing, reducer ownership, ordinary autostart dispatch lane, admission, restart backoff, and duplicate-owner resolution.
8. No new dependencies, queue namespace, lifecycle store, broker SQL, polling cadence, compatibility framework, or process pool. No whole-queue scans hidden behind a new helper name. Test only states produced by supported writers, administrative queue operations, real process exits, or a named broker failure.

Counterarguments: local completion retention uses more queue space after exit; atomic two-address publication increases fixed write work and PostgreSQL lock scope; exact service references add one field to state copying. Those costs buy removal of repeated shared discovery while preserving a single durable fact and existing recovery model. Validate them rather than assuming they are negligible. A permanent inability to write broker records cannot yield guaranteed durable completion; preserve bounded retries and visible operational failure instead of inventing a success proof.

## Proposed Spec Delta

Promotion strategy: **B, atomic per slice**. Apply each slice's exact requirement edits with its code, tests, reciprocal code references, and implementation mapping changes in one reviewable change. Do not promote the entire new protocol ahead of its producer and cleanup changes. The implementation promoted the requirement edits with their owning code and tests in commit `185a7c7ba42d23f37dc96eb7a20bd543984466c5`. The specs are the sole contract; the proposal below is historical context.

| Spec file | Sections | Owning slice |
| --- | --- | --- |
| `05-Message_Flow_and_State.md` | [MF-3] terminal publication; [MF-5] local completion/observation/cleanup | 1 |
| `01-Core_Components.md`, `05-Message_Flow_and_State.md` | [CC-2.3] TaskMonitor cleanup; [MF-2.1] cleanup/lifecycle notes | 1 |
| `07-System_Invariants.md` | [OBS.1], [OBS.13.9] completion publication and lifetime | 1 |
| `03-Manager_Architecture.md` | [MA-1.6a] post-exit proof custody, sentinel membership, and pending request references | 1, 2, 3 |
| `05-Message_Flow_and_State.md` | [MF-1] abnormal reconciliation; [MF-3.1] candidate evidence | 4, 5 |

### [MF-3]: replace the one-shot-only terminal publication requirement

> A task publishes completion through its existing lifecycle-reporting path. A task-owned terminal record is both a lifecycle event and a typed terminal envelope: it retains the lifecycle fields, including the redacted TaskSpec, and includes `type="terminal"`, `source="task"`, `tid`, `status`, `timestamp`, and applicable `error` and `return_code`. The same serialized record is atomically published to `weft.log.tasks` and the configured task `ctrl_out`, using SimpleBroker's public exact-destination broadcast with missing destinations created. Duplicate destination names receive one copy. Nonterminal persistent `work_item_completed` boundaries are published to the same two destinations, without the terminal discriminator. Other nonterminal lifecycle events remain global-log events.
>
> This completion publication applies to every BaseTask terminal path, including pipeline/edge tasks, interactive startup/finalization, cancellation, timeout, and failure. It replaces separate task-owned global-terminal and local-terminal writes. A work-item boundary remains nonterminal. Completion records use the existing bounded completion-write retry policy; retry exhaustion remains reported and nonfatal to process shutdown. Retrying the same serialized fact may leave duplicate records after an ambiguous commit; readers do not turn duplicate observations into duplicate output settlement.
>
> Completion is published after the task has finalized the output belonging to that boundary. Interactive STOP/KILL records the terminal state on the owner thread, unwinds the session, and publishes completion after its final stdout/stderr frames, before the post-unwind acknowledgement. It does not publish local completion while the session can still produce final output. The payload for one publication attempt series is serialized once and reused on retry.

Keep the existing typed-envelope fields, ordinary-reply exclusion, and Manager-source `wrapper_lost` rules. Amend implementation mapping references to the unified owner; remove claims that `_send_terminal_envelope` remains a separate writer.

### [MF-5]: replace the paragraph beginning “The watched completion surfaces”

> Ordinary result waits with known immutable task configuration observe the configured outbox and `ctrl_out`, plus the existing task-local runtime wake/evidence surfaces. They do not subscribe to or scan `weft.log.tasks` for live completion. Task-local completion records carry the same lifecycle fact as global history; existing terminal precedence, persistent work boundaries, output grace, and claim settlement apply.
>
> Bare-TID configuration discovery retains the existing bounded global-history search and terminal Monitor fallback. While configuration is unresolved, its global-log interest remains a valid discovery wake source; it is detached as soon as routes and mode are established. Negative discovery retains its inspected horizon. Explicit lifecycle-event following retains global-log observation to preserve the public event stream, including nonterminal progress events. It is distinct from ordinary result collection and is not claimed to have constant cost with respect to unrelated history.
>
> Global history remains available for status/history reconstruction and historical tasks written before local completion publication. Existing accepted-submission entry points use local completion from their first read, including before child initialization. Public reopened handles and `result(tid)` enter through bare-TID acquisition, which retains discovered historical terminal/batch evidence and configuration, including the terminal Monitor fallback. The decision uses the existing accepted-submission entry path, not inferred age/status, queue emptiness, or a new version flag. Configuration acquired from history is not a newly accepted submission. Once routes are resolved, local completion supplies new boundaries; discovery and local copies of the same completion are not appended twice. A retained old completion absent from local queues remains usable historical evidence. No live historical producer is supported across the coordinated cutover.
>
> Result/control drains preserve every valid completion record in a configured `ctrl_out`, including records for another TID sharing that queue. They fold only the requested TID and never consume those records as stream noise. An identical serialized persistent boundary observed again after a publication retry is the same boundary, even when its broker message ID differs.

### [MF-5]: replace standard task-control cleanup lifetime text

> Normal task exit may clear standard `T{tid}.ctrl_in`, but retains standard `T{tid}.ctrl_out` as result-completion evidence. For a proven-dead task, standard `ctrl_in` and inbox remain immediately stale under existing cleanup rules; standard `ctrl_out` is retention-gated alongside outbox, using terminal-message age when available and TID age only for the existing name-derived no-record fallback. The same rule applies to forced-process residue and disposed stale service owners. Claimed-row cleanup follows the same lifetime. Custom/shared queues remain outside automatic standard-queue deletion. Result observers read completion records without consuming them. Runtime-state queues remain runtime-only and excluded from persistence features.

Preserve reserved policy and summary-before-disposition requirements. Update both recorded-family and name-derived cleanup, stale-service-owner cleanup, retention-deferred scheduling, and pruning's queue-role classification so no path still calls `ctrl_out` immediately stale. Do not change the public pruning family/flag vocabulary.

Apply the following exact companion replacements to duplicated normative cleanup descriptions, not just the later [MF-5] paragraph:

- [CC-2.3] TaskMonitor description and [MF-5] terminal-runtime-worker description: replace the sentences declaring `ctrl_in`, `ctrl_out`, and inbox stale and outbox alone retention-gated with:

  > Standard `T{tid}.ctrl_in` and `T{tid}.inbox` are stale at terminal cleanup time. Standard `T{tid}.ctrl_out` and `T{tid}.outbox` are retained until task-log retention age, measured from terminal evidence when available and TID age only under the existing fallback. Reserved queues retain their separate cleanup policy.

- In both of those descriptions, replace stale/superseded-service-owner authority sentences with:

  > A stale/superseded service-owner disposition may authorize standard `T{tid}.ctrl_in` cleanup immediately and standard `T{tid}.ctrl_out` cleanup only at result-retention age. This does not turn the owner row into terminal lifecycle truth or authorize deleting its inbox, outbox, reserved, global, or custom queues.

- [MF-2.1], `Cleanup Boundary`: replace its first task-owned cleanup bullet with:

  > Task-owned cleanup clears standard task-local `T{tid}.ctrl_in` whenever the task unwinds through `cleanup()`, including success, failure, timeout, STOP, and KILL. It retains standard `T{tid}.ctrl_out` as completion evidence until the [MF-5] retention policy permits deletion. Outbox remains outside task-owned cleanup. A retained standard `ctrl_out` after terminal completion is expected, not forced-death residue.

- [MF-2.1], `Current Queue Lifecycle Management`: replace its task-cleanup bullet with:

  > Task cleanup closes task-owned handles, clears standard task-local `T{tid}.ctrl_in` rows, and retains standard `T{tid}.ctrl_out` completion evidence under [MF-5]. Custom queue ownership remains unchanged.

### [OBS.1]: replace the one-line requirement

> **OBS.1**: Lifecycle changes are written to `weft.log.tasks`. Task completion and persistent work-item boundaries are additionally addressed to the task's configured `ctrl_out` as the same serialized fact in one atomic broker publication, as specified by [MF-3]. This is routing of queue evidence, not a second lifecycle authority.

### [OBS.13.9]: replace normal task-control cleanup paragraph

> **OBS.13.9**: Normal task cleanup clears standard task-local `T{tid}.ctrl_in` before process exit and retains standard `T{tid}.ctrl_out` until the result-retention age. Terminal disposition may remove residual standard `ctrl_in` and inbox after required summary emission; standard `ctrl_out` and outbox remain retention-gated under [MF-5], including forced-death and stale-service-owner cleanup. Standard reserved remains owned by its existing cleanup policy. Custom/shared queues remain externally owned. Monitor repair covers only reachable current-writer/current-cleanup states; older Monitor schemas use migration, not a permanent compatibility lane.

### [MA-1.6a]: replace the sentinel-adapter membership sentences

> The sentinel adapter has one observer thread, one lock-protected membership dictionary keyed by TID, the existing coalesced membership/stop pipe wake, and fired `(tid, sentinel)` identities. The Manager registers or removes one child at each authoritative child-registry mutation. The observer snapshots membership when preparing a standard `multiprocessing.connection.wait`; the reactor does not rebuild all membership on each launch. Identical re-registration preserves exit suppression; replacement and removal invalidate stale identities. The Manager reactor alone validates identities, mutates the child registry, and reaps children. Adapter failure remains fatal, and shutdown joins it and closes its endpoints before the retained strategy closes.

### [MA-1.6a]: insert after post-exit terminal-proof grace

> After child exit, the Manager retires child bookkeeping and acknowledges any remaining unacknowledged reserved spawn request only after observing task-owned local terminal proof or committing a Manager-source `wrapper_lost` envelope. A failed terminal-proof read is unknown, not terminal proof. A failed failsafe publication retains the existing exited-child record and any unacknowledged reserved request, with a bounded retry deadline exposed through the existing reactor timeout path. It neither spins on an expired grace deadline nor waits for unrelated queue activity. Task-owned terminal proof still wins if it arrives later. Bounded Manager shutdown may end retries at its existing deadline; it leaves unresolved reserved work under the existing recovery policy rather than asserting successful acknowledgement.

### [MA-1.6a]: insert after the reducer-ownership requirement

> Pending service evidence first uses a live tracked candidate or an in-flight launch when the reducer gives that evidence precedence. Otherwise, Manager-local service state may retain one exact pending spawn-request message ID. Pending evidence from that reference requires a non-consuming exact lookup and trusted service-key validation in the same source/reserved queues currently in scope. An absent or invalid reference triggers durable recovery scanning; a successful scan may retain one matching ID or prove absence. Failed reads or partial scans do not prove absence or clear the reference. Restart reconstructs pending evidence from queues. ID-preserving reserve/requeue operations remain visible without a timestamp watermark. The reference is not lifecycle authority and does not alter service dispatch lanes or reducer decisions.

### [MF-1]: insert in submission reconciliation requirements

> During one abnormal submission-reconciliation observation, retained task/child spawn evidence and an inspected task-log horizon prevent replay of already inspected history. Both exact top-level `tid` and manager `child_tid` records remain supported. Queue presence, reservation, spawned proof, and rejection keep their existing precedence. Administrative backfill behind a completed horizon requires a fresh observation, consistent with [MF-5].

### [MF-3.1]: replace “current liveness checks use” bullet

> Endpoint resolution reads lifecycle and runtime evidence only for matched candidate owner TIDs, using shared task-evidence classification. Terminal proof retains precedence over live runtime handles. An empty candidate set performs no history or task-state reads. Candidate filtering bounds transferred/decoded history; it does not claim that public broker substring search is an indexed TID lookup. Task processes publish host-PID liveness through `runtime_handle` when no runner-specific handle exists; there is no separate endpoint lease or heartbeat contract.

## Rollout and rollback

Slice 1 is a protocol/lifetime change. Drain ordinary old-version work, stop old managers and persistent services, then restart upgraded producers, cleanup owners, and readers together in the context. This avoids a permanent runtime feature-negotiation mode for old live persistent tasks that lack local batch boundaries. Do not describe a rolling mixed-version cutover as supported. Historical completed tasks continue through existing history/Monitor acquisition fallback. Test that fallback before cutover. If a context cannot drain old work, stop and design a separate bounded migration; do not silently enable local-only waits there.

Keep the global log and existing envelope keys additive, so old history/status consumers can read new records. Before rollback to a version that immediately clears `ctrl_out`, drain upgraded live work and stop upgraded result observers. Restore the matched runtime/spec changes together. No data migration, queue renaming, destructive history rewrite, or new dependency is required. Reverting slices 2–5 is independent of the completion protocol once their own code/spec/test changes are reverted together.

Post-cutover, compare 100/200/300 accepted submissions in one context with ordinary result waits, normal cleanup enabled, then a burst containing task failure and abrupt wrapper death. Record enqueue transaction time separately from end-to-end time, queue wait, child launch, and result observation. Do not infer critical-section growth from admission wait or process startup.

## Implementation slices

Each slice begins with the listed real-path regression, ends with focused checks and independent review, and stops if it needs an unplanned authority, cleanup lifecycle, polling loop, or public contract. Keep changes small within each slice; Slice 1's producer/reader/cleanup changes are coupled and must land together.

### 1. Address completion facts to their observers

**Read first:** `BaseTask._report_state_change`, `_write_state_queue_message`, the baseline `_send_terminal_envelope` (removed by this implementation), `_cleanup_standard_control_queues_on_exit`; Consumer finalization/failure, pipeline/edge completion, interactive startup/finalization, Manager child-terminal proof, `LogFold`, result materialization and `_CollectedResult`, realtime event iteration, TaskMonitor runtime-control and dead-task cleanup policies. Understand why a persistent output is not terminal and why deleting control evidence at exit invalidates local-only waiting.

**Files to modify:** `weft/core/tasks/base.py`, `consumer.py`, `pipeline.py`, `interactive.py` (as applicable to actual writers); `weft/core/manager.py`; `weft/core/task_evidence.py`; `weft/commands/_result_wait.py`, `result.py`, `events.py`, `tasks.py`; `weft/core/monitor/policies/runtime_control.py`, `dead_task.py`; `weft/core/pruning/retention.py` and task-monitor cleanup ownership only where classification requires it; the three governing specs. Update direct callers of the removed separate terminal writer, not wrappers that emulate it.

1. Extend the existing completion publication owner, not a new publisher service. Build and redact one lifecycle payload. On terminal status add existing terminal-envelope keys from canonical TaskSpec state; on `work_item_completed` retain the existing boundary payload. Publish with `broker.broadcast(serialized, queue_names=(WEFT_GLOBAL_LOG_QUEUE, configured_ctrl_out), create_missing=True)`. Exact destinations plus creation are essential: without `create_missing=True`, installed SimpleBroker enumerates existing queues. Reuse the existing bounded retry loop for both completion categories; ordinary nonterminal reporting retains its current best-effort behavior. Remove separate task terminal-envelope emission and update its callers/mappings. In `InteractiveTaskMixin._interactive_finalize_session` and `_interactive_handle_control`, move/defer the terminal report to after session unwind and final stdout/stderr frames. Retain the intended event name/details locally until that owner-thread finalization point, rather than publishing early and adding a second completion. The no-session startup/control branch reports immediately. Verify all terminal paths, including pipeline/edge, report through this owner after their boundary output is finalized.
2. Retain standard `ctrl_out` on BaseTask exit and through every TaskMonitor/dead-name/stale-service cleanup path until result retention. Adjust deferred-retention scheduling so a young retained control queue does not cause an immediate cleanup loop. Reuse the current terminal-age policy; do not create a per-reader lease/ack registry or selective row-retention scanner. Custom queue ownership remains unchanged.
3. Read/fold local lifecycle completion records incrementally through the existing result materialization and evidence helpers. Keep one lifecycle fold and one selected boundary stream, not independent global/local policy accumulators. Accepted-submission entry points (`_accepted_taskspec` / `_accepted_result_materialization`) start local-only; public `client.task(tid)` and `result(tid)` retain bare-TID history/Monitor acquisition. These existing call paths supply the distinction; do not inspect status/age or add a protocol flag. After discovery, prefer local copies for batch anchors; do not append the discovered global and local copy of one fact as two boundaries. If old historical completion has no local copy, retain that discovered proof. Local acquisition has its own inspected horizon: never carry a global discovery cursor or global batch-start horizon into the local queue, because the two broadcast copies have different IDs. Within the local stream, skip identical serialized boundary bodies from ambiguous-commit retries using observation-owned membership, without rescanning the prior boundary list. Keep the first local boundary ID for that fact. Local message IDs anchor local batch observation; output written before the completion broadcast precedes both broadcast IDs. Update `drain_ctrl_out_stream_messages` to retain valid completion records for every TID, folding only the requested TID; it currently deletes foreign terminal and all batch records as noise. Preserve task-vs-manager precedence, completion grace, readable/claimed distinctions, output spill/stream handling, and final drain. Unknown configuration retains its current bounded discovery cursor/wake source and detaches global interest once known. Explicit progress/lifecycle events retain their global event stream; do not silently reduce `follow_task_events` to completion-only events.
4. Manager child-exit proof for upgraded live children uses configured task-local typed terminal records; remove its per-exit global suffix replay. Atomic task publication eliminates the state where new global terminal proof is durable but its local counterpart is absent. Keep the original clean-exit grace, nonzero-exit handling, PID fallback, Manager-source `wrapper_lost`, and task-owned precedence. A proof-read failure is unknown; attempt the failsafe rather than treating the read error as proof. Make failsafe publication report whether it committed, reusing the existing bounded terminal-write attempts. Remove the child/acknowledge any remaining unacknowledged reserved spawn row only after task proof is observed or a failsafe commits. On failure retain the existing child record; add one optional retry deadline to that record, using the existing terminal-proof grace interval and `_manager_child_timeouts`. Respect that deadline before broker work, and do not also publish an expired grace deadline. Shutdown still obeys its existing overall deadline and preserves unresolved reserved work. This is one child-owned retry fact, not another worker or retry registry. Do not use stale runtime state as a verdict or claim durable completion while the broker remains unavailable.

**Firing proof:** real broker-backed command/client waits for one-shot success/failure/timeout/cancellation, persistent multiple work items (including empty batches), interactive startup failure, stream output, pipeline/edge success/failure, and custom routes. Extend existing `test_burst_result_wait`, terminal-event retry, consumer-terminal, interactive, pipeline, terminal-handoff, TaskMonitor, and result tests rather than adding a new matrix harness. Kill a real managed wrapper before terminal publication and assert Manager `wrapper_lost`; let a real task verdict arrive during/after grace and assert it wins. Fail the Manager's first failsafe publication at the broker boundary: its exited-child record/reserved row remain, next wait has a bounded nonzero retry timeout, and successful retry wakes the ordinary result waiter. Verify bounded shutdown under the same outage. Run cleanup between publication and collection and prove terminal/batch evidence survives; exercise retention expiry with existing clock fixtures. Exercise atomic publication failure at the broker boundary and assert no split global/local fact. Retry exhaustion has its existing visible diagnostic and shutdown behavior. Preserve old completed-task history/Monitor retrieval and progress event streams.

Add direct regressions for two tasks sharing `ctrl_out` (one drain must not delete the other's terminal/batch proof); a retried identical persistent completion must not appear as a second empty batch; delayed interactive STOP/KILL output must be observed before terminal return; and bare-TID pre-cutover failure/empty persistent or pipeline completion must retain its verdict. An output-only historical success test is insufficient because fallback output can conceal missing terminal evidence.

**Complexity proof:** with 100/200/300 accepted-submission waits and unrelated global activity, count all broker work caused by observation, including watcher threads. Assert zero global completion searches/subscriptions for these waits, not merely zero JSON decoding on the caller thread. A bare-TID wait may search for initial configuration; after discovery it performs no recurring global completion search. Hold local output/lifecycle volume fixed while varying unrelated log size. Verify exact-destination broadcast on SQLite and PostgreSQL; PostgreSQL 4.5.1 locks the meta timestamp row then the messages table in SHARE ROW EXCLUSIVE mode. Measure that fixed contention cost and reject the slice if it recreates the burst bottleneck. Fix broker-level broadcast issues in SimpleBroker rather than writing private SQL or a Weft transaction shim.

**Stop gate:** any terminal path still depends solely on global history, any cleanup path deletes young local completion evidence, or a design requires per-reader cursors persisted in queues, a new bus/store, or broad mocked lifecycle tests.

### 2. Register child sentinels incrementally

**Read/modify:** `_ManagerChildSentinelAdapter`, `_commit_child_launch_success`, `_cleanup_children`, `_terminate_children`, event drain/close and manager wait deadlines in `weft/core/manager.py`; `tests/core/test_manager.py`; [MA-1.6a]. Read [CC-2.2.1]/[IMPL.10] for reactor/resource ownership. Understand why a fired identity can be stale while a replacement is current.

Replace full `publish(membership)` with `add(tid, sentinel)` and `remove(tid)` on the existing adapter. Mutate one dictionary entry under its existing lock and signal the existing coalesced pipe. Retain suppression for the same pair; discard the old suppressed identity on replacement/removal. Snapshot the dictionary only in the observer when building its next standard wait set. Convert the returned ready list to a set once, and validate a fired pair with `membership.get(tid) == sentinel`. Do not add a mutation journal, generation protocol, second registry, or selector abstraction. Bind add/remove to the authoritative registry commit/reap/termination sites, including failure cleanup.

Extend existing coalescing, observer failure, close, late launch-worker cleanup, and reactor ownership tests. Add replacement/removal while waiting and rejection of a queued stale result. Use actual multiprocessing exit for crash coverage. A narrow counting test may inspect registration work, because that is the performance contract: 100/200/300 child commits access 100/200/300 sentinel values, rather than 5,050/20,100/45,150. Do not use wall-clock thresholds or fake child states to replace real exit tests.

Standard wait snapshots and CPython `BaseProcess.start()` cleanup still cost O(C) in live children. Existing child sweeps/deadline scans remain. Measure them; preserve crash coverage rather than concealing these costs behind more bookkeeping. Stop if the slice starts replacing process lifecycle or shutdown policy.

### 3. Confirm known autostart requests by exact ID

**Read/modify:** `weft/core/manager_services.py::ManagedServiceState`, `_copy_service_state`, reducer precedence; `weft/core/manager.py::_reconcile_managed_services`, `_pending_service_keys`, `_enqueue_managed_service_request`, `_apply_managed_service_decision_state`, launch commit/disposition; `tests/core/test_manager.py`; [MA-1.6a]. Understand why a bare timestamp cursor misses requeued old requests and why a process-local pending boolean cannot prevent duplicates after restart.

1. Compute tracked service candidates before pending queries. Skip pending queries for keys whose live tracked ownership already controls the reducer. Subtract keys proved pending by `_active_child_launches`; return without scanning when no unresolved keys remain. Do not skip the existing duplicate-owner/terminal-proof audits merely because a tracked child exists.
2. Add one optional `pending_request_id: int | None` to existing `ManagedServiceState`, alongside the current reducer's `spawn_pending` decision field. Capture `Queue.write()`'s committed return value only after successful service enqueue. Copy/apply this field through the existing state paths, so reducer copying cannot lose it. No separate map, timer, journal, persistence format, or queue-location cache.
3. For unresolved keys with references, use `peek_one(exact_timestamp=id)` in each existing pending-query source/reserved queue (normally two internal or up to four autostart queues). Keep ordinary unclaimed semantics. Decode and revalidate with `_spawn_request_service_key`; an ID alone does not establish trusted service identity. A matching row proves pending; a move/requeue remains visible by the same ID.
4. For absent/invalid references or cold restart, use the existing full recovery iterator for remaining keys with `strict=True`. `iter_queue_json_entries` otherwise suppresses generator-opening broker failures into an empty iterator, which cannot safely prove absence. Stop once every requested key has matching evidence; retain one matching ID per key. Only a successful complete recovery scan may prove absence and clear the reference. Broker errors/partial scans retain the hint and existing failure boundary. A removed reference followed by another matching duplicate is recovered by this same scan.
5. Clear a matching reference on launch ownership handoff and definitive disposition, without clearing a newer request's reference. Keep the public autostart lane, service backoff, manifest removal, once/ensure policy, and reducer decisions unchanged.

Use real queue rows and Manager convergence in existing tests: live service and in-flight launch with hundreds of unrelated public rows cause no pending backlog scan; a known pending request uses a fixed number of exact lookups as backlog grows. Restart from retained inbox/reserved rows suppresses duplicate enqueue. Exercise ID-preserving reserve/requeue, deletion followed by another duplicate, rejected trusted metadata, enqueue failure, scan/read failure, and existing failed-launch reserved policies. Reuse ordinary Manager/process tests for abrupt child death, immediate restart, manifest deletion, and backoff. Do not cross-product every input with every queue if existing tests already cover the contract.

Cold recovery and truly missing/uncertain service evidence remain O(Q). That is the honest price of discovering an unknown predicate in shared JSON. This slice removes repeated scans while ownership or exact pending identity is known. Stop if strict cold O(1) is demanded: that requires a separately reviewed durable routing/index change, not a permanent boolean or cursor shortcut.

### 4. Remove smaller repeated rediscovery work

**Files:** `weft/core/tasks/base.py`, `weft/core/manager.py`, `weft/commands/_spawn_submission.py`, their existing focused tests, and [MF-1] plus nearby implementation notes. Three independent small changes; review each against its current code.

- Streaming: assign the returned `Queue.write` ID directly to `_streaming_session_message_id`; exact-delete that owned marker at completion. Remove the normal write-then-search. Keep only recovery behavior reachable from supported state; do not construct a marker-without-ID test solely to retain a dead fallback. Prove a real stream's marker is registered and removed amid unrelated streaming sessions, with enqueue failure leaving ownership unset.
- Info logging: use `heapq.nsmallest(MANAGER_SERVE_LOG_CHILD_LIMIT, child_items, key=...)` for the existing lowest-TID sample. Preserve state-change-triggered emission and rate-limit semantics. Moving the current rate gate ahead of state construction would suppress changes and is not behavior-preserving. This reduces sorting from O(C log C) to O(C) for fixed sample size, not O(1). Prove emitted sample and truncation count, without an artificial logger framework.
- Abnormal spawn reconciliation: retain one observation-local captured horizon and latest matching spawn/rejection evidence, advancing only after fully inspecting that range. Reuse public queue-window helpers with `strict=True`: their default can suppress an iterator-opening failure into an empty iterator, which must not advance a cursor. Retain both `tid` and `child_tid` matching and all existing outcome precedence. Do not cache a negative queue lookup across moves. Direct queue-first reconciliation with unrelated log growth proves old ranges are not reread and a late rejection is still seen; inject a real broker open/read failure, then prove the rejection is still found after recovery. Keep registration-before-read and cancellation behavior. This is a fallback improvement, not a claim that normal acceptance previously scanned the log.

### 5. Bound candidate decoding and verify the complete burst path

**Files:** `weft/core/endpoints.py`, existing endpoint/task-evidence tests, [MF-3.1], and final traceability notes in the touched specs/plan. Read the current candidate-first state lookup and shared terminal-priority rules before changing them.

Replace the unconditional global `_latest_task_statuses` pass with existing candidate-TID history/evidence acquisition for matched endpoint owners, retaining exact TID filtering, pagination, terminal precedence, and runtime-only mapping semantics. Reuse `task_evidence.read_task_history`/classification; do not add a name cache, authoritative live-bit, or SQL index. Empty candidates read neither state nor history. Test endpoint resolution with unrelated global history, a terminal owner with a still-live host, competing owners, and missing/malformed evidence. Report backend substring-search cost separately: candidate filtering alone does not remove O(H) broker search. If measurements show a regression for many candidate owners, retain the existing single-pass list strategy for that explicitly bulk operation; do not apply many point scans blindly.

Run the end-to-end burst exercise with ordinary configured waits and services enabled. Separate N pending requests, C live processes, W ordinary result waiters, H retained history, and S desired services. Verify the following structural outcomes rather than a single machine-dependent latency target:

| Path | Required bound / intentional residual |
| --- | --- |
| Ordinary enqueue | Fixed request insert work; no new pending-count query. |
| Configured result observation | Work follows that task's output/completion facts; no shared-log completion wake/search. |
| Reactor sentinel membership | One entry added/removed per child; standard wait/start internals remain O(C). |
| Healthy/pending autostart | No backlog scan for live/in-flight proof; fixed-scope exact lookup for a known request. |
| Cold service recovery | O(Q) only when pending identity is unavailable/invalid; no false duplicate start. |
| Bare unknown TID / explicit event follow | Retained discovery/progress semantics; not claimed O(1) in global history. |
| Admission / child audit / registry / endpoints | Costs reported separately; no crash sweep or strict-schema validation removed to improve a graph. |

If ordinary per-turn child sweeps or service-registry scans dominate after the first three fixes, record measured counts and propose a separate narrow plan. Do not opportunistically introduce heaps, duplicate counters, shared fan-out daemons, or schema repair caches into this implementation.

## Verification and completion gates

Use `. ./.envrc`, then repo-managed `.venv` tools. Do not install new dependencies or assume global pytest/ruff/mypy. Keep WeftTestHarness, broker queues, reservation, cleanup, and real process death real. Mock only an actual failure boundary or instrument a narrow cost counter. Existing subprocess/event synchronization replaces timing sleeps. Consult the test-audit skill if writing/reviewing test changes; this plan authors no runtime tests.

For each slice run its focused command/client/task/Manager/Monitor tests with `-n 0` when measuring operation counts, followed by affected spec-contract tests. At implementation completion run:

```bash
. ./.envrc
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
```

Use the repository's PostgreSQL test harness/environment for public broadcast, notifier activity, and burst behavior before claiming backend readiness. SQLite proof alone is insufficient for the table-lock tradeoff. Run plan metadata/spec hygiene and the repo traceability gate; update implementation mappings, removed helper references, spec backlinks, and reciprocal module/function docstrings atomically with their owning slice. Keep an explicit file-list change review; no drive-by configuration or CI edits. Record evidence and promotion baselines, and commit only with user authorization. Until then report the reviewable uncommitted state, not landed implementation.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |
| [MA-1.6a] | Post-exit custody wording could imply all spawn reservations remain until exit. | Preserve normal successful-launch acknowledgement; retain and retry any unacknowledged residual reservation until post-exit proof commits. | Source review found `_handle_child_launch_result` already acknowledges a successful launch. Changing that boundary would alter the existing reservation contract. The crash test now injects launch-ack failure through that owner to create reachable residue. | Clarified [MA-1.6a] post-exit paragraph in `03-Manager_Architecture.md`; no launch-ack protocol change. |

## Implementation evidence, 2026-10-12

All five slices are implemented in commit
`185a7c7ba42d23f37dc96eb7a20bd543984466c5`, with final observation-test
adaptations in `c22064d0` (both commits verified in Git). The first commit
contains the runtime and spec promotion from baseline
`c573926ad0442f79f4712879e7693d416b3fd52f`. Final verification covers both
commits. The user authorized the final evidence commit and closeout on
2026-10-12. Implementation, independent review, and verification are complete;
the existing traceability debt is recorded below.

The completion owner now atomically addresses one serialized lifecycle fact to
history and configured control output. Result and control observers retain that
proof. TaskMonitor retains standard control output until its existing retention
expiry; custom/shared routes keep external custody. Manager post-exit proof reads
only local control output and retains exited-child custody across a failed
failsafe write. Incremental sentinel identities, exact service-request hints,
retained streaming IDs, incremental abnormal spawn observation, fixed-size log
samples, and candidate-scoped endpoint evidence complete the remaining slices.

Independent implementation review found and corrected three interactions:
external interactive signals/parent loss needed the same final-output deferral as
queued STOP/KILL; terminal activity/mapping could let a control client terminate
a wrapper before completion committed; and the stop command destructively read
local terminal proof. The corrections use the existing finalizer, lifecycle
reporter, and completion predicate. A real child with a deliberately blocked
broker transaction and the actual stop command proves the publication boundary
and retained proof. Owner-delivered SIGTERM, SIGUSR1, and parent loss exercise
final-frame ordering. These are reachable production paths, with no test-only
runtime guard or new lifecycle state machine.

Structural controls against the original implementation reproduced global reads,
error-as-proof and premature child retirement, and 5,050/20,100/45,150 sentinel
member visits for 100/200/300 launches. The new membership path visits only the
100/200/300 new identities. Accepted-observation probes count all threads and
show zero global completion searches/subscriptions at those sizes; abnormal
bare/discovery observation deliberately retains its global watcher and measures
its owner's incremental history reads separately.

End-to-end evidence: all 600 tasks completed across 100/200/300-task runs with
three built-in services and eight ordinary configured waiter workers. Existing
SQLite admission cap 12 bounded tracked/in-flight children at seven (three
services plus four public children); admission capacity deliberately limited
throughput. An enqueue-only public-path run left
exactly 300 requests pending; successive 100-position windows had request-insert
p50 0.174/0.153/0.155 ms and p95 0.362/0.276/0.235 ms. Whole `client.submit`
p50 was 1.879/1.790/1.825 ms and p95 3.854/2.227/2.304 ms. Admission deliberately
held public execution, separating queue growth from process startup. Backend
contention and final verification results follow below.

| Burst size | Public submit wall | Insert p50 / p95 | Whole submit p50 / p95 | All results collected |
| --- | --- | --- | --- | --- |
| 100 | 1.802 s | 0.438 / 12.286 ms | 8.551 / 72.390 ms | 100/100 in 65.384 s |
| 200 | 2.665 s | 0.420 / 6.317 ms | 7.088 / 40.573 ms | 200/200 in 147.824 s |
| 300 | 1.259 s | 0.180 / 0.542 ms | 2.109 / 5.011 ms | 300/300 in 245.193 s |

These end-to-end wall times overlap host tests and include bounded admission and
process startup; they are not clean throughput comparisons. The N=300 audit
captured all lifecycle stages: request-to-spawn p50/p95 119.382/231.175 s,
spawn-to-running 0.188/1.025 s, and median launch gaps by successive 100-launch
windows 1.004/0.978/0.979 s. The configured one-second admission recheck explains
the spacing; it is excluded from the enqueue-complexity claim. Two probe teardown
warnings named service PIDs as survival-uncertain; both PIDs were checked absent
immediately afterward. All owned child processes were cleaned up.

Residual costs are intentional: standard process/wait and child audits scale with
live children; cold service recovery walks a backlog when identity is unknown;
explicit service/history audits and bare-TID discovery read history; endpoint
substring search still depends on retained history. Shared/custom control routes
and persistent local history can contain many facts. No blanket O(1) claim is
made for those paths. Two-address publication adds fixed broker contention and
retained local evidence adds storage. Coordinated producer/consumer restart is
required for the new protocol; no mixed-version compatibility shim was added.

### PostgreSQL publication measurements

Quiet-host probes used SimpleBroker 8.5.1 and simplebroker-pg 4.5.1, a 1,277-byte
completion body, fresh schemas, and connections established outside the clock.
Each case ran eight writers with 64 operations each (512 operations), in two
counterbalanced passes. The comparison is the actual public two-address atomic
broadcast versus two separate public queue writes. These are paired operation
latencies, not per-row timings.

| Pending submissions | Atomic broadcast p50 / p95, pass 1 | Atomic broadcast p50 / p95, pass 2 | Two writes p50 / p95, pass 1 | Two writes p50 / p95, pass 2 |
| --- | --- | --- | --- | --- |
| 100 | 10.635 / 13.908 ms | 10.555 / 15.679 ms | 24.307 / 37.491 ms | 22.407 / 34.693 ms |
| 200 | 11.202 / 17.112 ms | 9.848 / 15.908 ms | 22.558 / 36.760 ms | 23.705 / 39.593 ms |
| 300 | 9.372 / 11.606 ms | 9.945 / 13.615 ms | 27.277 / 36.459 ms | 25.592 / 37.171 ms |

The eight-writer runs show no monotonic backlog-dependent growth over this range;
atomic publication achieved 669–836 paired operations/s versus 247–331 for two
writes. A single-writer, 128-operation control at 200 pending submissions
measured atomic p50/p95 1.193/1.504 ms versus 1.710/2.431 ms for two writes.
Earlier probes overlapped other host work and produced tails up to 170 ms; the
quiet counterbalanced runs supersede those absolute-latency measurements.
This resolves the measured lock-scope tradeoff for the tested workload, not a
literal backend O(1) claim or a production throughput guarantee. The real
600-process burst exercise above used SQLite; no PostgreSQL process-burst result
is claimed.

### Final verification

The checkout was frozen for the final full runs. The repo-managed toolchain,
loaded through `.envrc`, reported:

- Fast suite: **5,531 passed, 33 skipped**, 285.10 s.
- Full suite including slow tests (`pytest -m ""`): **5,532 passed, 44 skipped**,
  259.76 s. Skips cover backend-specific cases and opt-in live-provider tests.
- Real PostgreSQL focused runtime suite: **938 passed**, covering completion,
  retry, interactive finalization, result observation, Monitor retention,
  Manager bookkeeping, endpoint evidence, and abnormal spawn observation.
- Final PostgreSQL control/connection suite: **153 passed**; both CLI
  cancellation variants then **passed** with the correct active environment
  (23.55 s). The initial CLI attempts exited before task startup because the
  sanitized test environment omitted `VIRTUAL_ENV` and
  `UV_PROJECT_ENVIRONMENT`, causing `uv run --active` to select Python without
  SimpleBroker. Restoring those two runtime variables required no code change.
  These targeted suites overlap; their counts are not a count of unique tests.
- Required full mypy roots: **453 source files, no issues**. Ruff check and
  format check passed (418 files already formatted). Final plan/spec hygiene
  checks passed (six tests); `git diff --check` passed.

Backstitch remains an existing non-clean gate: baseline **34 errors, 1,151
warnings, 654 informational findings**; final **34 errors, 1,142 warnings, 651
informational findings**. Comparing finding signatures without line numbers
shows **zero new errors or warnings**. This change does not waive the existing
34 errors or claim a clean repository-wide traceability gate. The same command
recorded in the review section was used on a complete baseline checkout and the
final checkout. Disposable PostgreSQL resources and the private test environment
file were removed after verification.

## Review record

Plan-only review must cover both the proposed spec delta and the code paths it would change. Independent review is required because completion, retention, and supervision cross process/queue boundaries. Prefer an available different-family reviewer; require PASS/BLOCKED on implementability and whether robustness would degrade. Also review for conceptual unity: could the design remove an abstraction or reuse an existing owner/primitive without losing a reachable failure case?

Fresh-eyes self-review: corrected the proposed scope to distinguish cheap accepted-submission observation from bare-TID discovery and explicit event following; retained the standard library's O(C) costs and O(Q) cold recovery rather than adding bookkeeping structures. Kept logging state-change semantics instead of moving a rate gate ahead of state capture. Checked exact broadcast selection and PostgreSQL lock order against installed source. Self-review also made local-vs-global cursor domains explicit and found duplicated normative immediate-control-cleanup text in [CC-2.3], [MF-5], and [MF-2.1]; exact companion replacements are now part of Slice 1. Residual risks are fixed broadcast contention, retained control-row volume, and the coordinated old-live-task cutover; implementation measurements/tests must resolve them.

Initial reviewer availability: Claude could not authenticate (expired OAuth); Gemini refused the headless untrusted-directory session; Qwen's configured model was unavailable. The initial independent review therefore used a same-family agent. A subsequent different-model review completed successfully as recorded below. No credentials, saved trust settings, or model configuration were changed.

| Finding | Evidence and disposition |
| --- | --- |
| R1: shared/custom control drains delete another task's completion | Accepted. Slice 1 and [MF-5] now preserve completion records for all TIDs and require a two-task shared-route regression. |
| R2: interactive terminal reporting precedes unwind/final frames | Accepted. Slice 1 and [MF-3] now defer unified completion to the existing output-finalization owner, including STOP/KILL and no-session branches; trailing output gets a reachable process/control test. |
| R3: default queue-window failure suppression makes a cursor unsafe | Accepted. Slice 4 requires strict reads, successful complete range inspection, and transient read-failure recovery proof. |
| R4: historical configured-vs-accepted entry was ambiguous | Accepted. The plan now names existing private accepted entry points versus public bare-TID reopen. It adds historical failure/empty completion proof, with no age heuristic or version framework. |
| R5: ambiguous retries/global discovery can duplicate a persistent boundary | Accepted. The plan now requires one selected stream, serialized-fact deduplication without prior-list rescans, and separate source horizons. |

Independent round-2 verification: **PASS** on R1–R5, with no remaining correctness blocker in the revised fixes. Corrected its remaining owner-name typo to `_interactive_finalize_session`. The self-review companion spec replacements synchronize the same reviewed retention rule across duplicated descriptions; they introduce no extra lifecycle. Implementation reviews must verify each slice and the final integrated state; planning approval is not implementation evidence.

### Different-model review, 2026-10-12

Completed with Gemini CLI in read-only plan mode, using its session-only `--skip-trust` option. The CLI exited successfully and reported model `gemini-3.1-pro-preview-customtools`. The reviewer received the complete plan/proposed spec delta, canonical context/spec/code reading instructions, and the user's explicit scope: simplicity, conceptual unity/weftness, existing Python/SimpleBroker primitives, reachable tests/guards, and crash/failure detection. It returned **PASS**, answering both that the plan was implementable confidently and that it would not degrade crash/failure detection or robustness. It reported no actionable findings.

Its non-actionable observations favored exact pending-request references, incremental sentinel membership using standard process waiting, and the coordinated cutover. No architecture, tests, or guards were added in response to those observations. Its shorthand description of exact lookup as O(1) is not adopted as a literal backend complexity claim: this plan promises a fixed number of indexed lookups, with broker index cost remaining a broker concern. Its favorable cleanup assessment refers to the proposed revised retention contract, not a claim that the baseline already retains `ctrl_out`.

Follow-up author source check: `weft/helpers/__init__.py::iter_queue_entries` suppresses generator-opening failures unless `strict=True`, and `_pending_service_keys` currently uses that default. Slice 3 now explicitly requires the existing strict option during recovery, matching its already specified failure-is-not-absence rule. This adds no new error type, guard, state, or test framework. The already planned read/scan-failure regression must exercise this production helper boundary and show that the reference survives and no duplicate service is enqueued.

Gemini round-2 verification of that narrow clarification also completed successfully with the same reported model and returned **PASS**: the existing strict-read option is the simplest primitive for the requirement and adds no new complexity defect. This closes the different-model review request. These are plan reviews, not evidence that runtime implementation or PostgreSQL contention checks have passed.

After the different-model review update, the six plan/spec documentation checks and `git diff --check` passed again. The deterministic traceability check was also rerun and retained the same 34 errors, 1,151 warnings, and 654 informational findings as the recorded baseline comparison below; it remains an existing non-clean gate.

Plan-authoring validation: `tests/specs/test_plan_metadata.py` and `tests/specs/test_spec_hygiene.py` passed (6 tests, serial); `git diff --check` passed. A deterministic Backstitch check against both this draft and a complete temporary checkout of the recorded baseline reported the same 34 errors, 1,151 warnings, and 654 informational findings, with zero new error/warning signatures (ignoring line shifts). The repository-wide traceability gate is therefore not clean; this plan does not waive that debt or claim the gate passed. The read-only comparison used the existing sibling Backstitch tool, without changing project dependencies/configuration:

```bash
../backstitch/.venv/bin/backstitch check --repo-root . --spec-root docs/specifications --plan-root docs/plans --code-root weft --code-root tests --test-root tests --format json
```


### Implementation review, 2026-10-12

Independent owner reviews covered Manager failure custody, reader source horizons,
shared-route retention, autostart recovery, and producer ordering. Review found
and fixed the signal and cancellation issues described in the implementation
record; no failure detector was removed or weakened. The final control predicate
is reused by result and control observation, rather than adding another envelope
schema or publisher. Tests exercise real producers, broker failures, administrative
queue moves/deletes, retained rows, and the actual stop command. Synthetic old
read-one-only clock fixtures were replaced by real queues with an owner-local
clock facade, avoiding global broker/driver clock changes.

Different-model implementation review used Gemini CLI with tools restricted to
read-only plan approval and external extensions/MCP disabled. Reported model:
`gemini-3.1-pro-preview-customtools` (the integrated session also listed
`gemini-3.6-flash`). Integrated review session
`5dc75db6-9fe8-476c-ab45-4d8516837c51` returned PASS. Independent firing tests then
exposed the signal and cancellation interactions that this review missed. The
corrected producer ordering was reviewed again in session
`1151985a-d4a4-4bb9-942b-32b95b2fc466`, PASS; final control proof custody and all
other control-output consumers were reviewed in session
`e6ade684-c9e9-4c66-bc02-35e2e00e6b4e`, PASS. These reviews support the design;
they do not substitute for backend or process verification.
