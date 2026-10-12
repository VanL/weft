# Direct Result Observation: Implementation Repair

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-3] (traceability only); docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/14-Python_API_Surfaces.md [PY-1], [PY-2]; docs/specifications/10-CLI_Interface.md [CLI-1.1.1], [CLI-1.2.1], [CLI-1.2.2]; docs/specifications/13C-Using_Weft_With_Django.md [DJ-12.1], [DJ-12.2]
Superseded by: [Result observation simplification](./2026-10-10-result-observation-simplification-plan.md)

Class: 4. This implements the promoted result contract across submission,
collection, realtime observation, and resource cleanup. Hardening is required;
no new product contract or normative spec revision is proposed.

## Goal and Authorization

Make callers wait for this task's result or failure through one shared
capability. Retain accepted configuration rather than reconstructing it;
filter bare-TID history before transferring bodies; share common collection
policy; and wait on activity and actual deadlines instead of result probes.

The owner authorized runtime implementation with “Please implement per plan”
on 2026-10-09, after the independent planning reviews recorded below. Execute
the five ordered slices below against the promoted requirements. Commit, push,
release/configuration changes and sibling-repository writes remain outside this
authorization.

Requested outcomes:

- [x] Cover every defect in the preceding implementation assessment, including
  duplicate common behavior and hidden point-TID snapshot/evidence reads.
- [x] Record precise owners, scope, preserved contracts, and firing proof.
- [x] Complete separate author fresh-eyes and independent Claude reviews.
- [x] Verify plan metadata, backlinks, scope, and preserved prior files.

## Source Documents and Required Reading

Read `AGENTS.md`, then the ordered `docs/agent-context/README.md` chain:
decision hierarchy, principles, engineering principles, relevant runbooks,
agent-context lessons pointer, and `docs/lessons.md`. Relevant runbooks are
writing-plans, hardening-plans, review-loops-and-agent-bootstrap,
runtime-and-context-patterns, testing-patterns, and adversarial-acceptance-probes.
Apply the test-audit authoring gate before adding or changing tests.

| Source | Governing boundary |
| --- | --- |
| [Architecture](../specifications/00-Overview_and_Architecture.md) | Existing durable spine; queues own truth; one current contract. |
| [Core components](../specifications/01-Core_Components.md) [CC-2.1], [CC-2.2.1] | Existing watcher/reactor foundation and drive ownership. |
| [TaskSpec](../specifications/02-TaskSpec.md) [TS-1], [TS-1.4], [TS-1.4A] | Shared template-to-resolved normalization and immutable execution sections. |
| [Message flow](../specifications/05-Message_Flow_and_State.md) [MF-5] | Accepted-config custody; filtered discovery; direct collection; shared code; evidence priority, modes, grace, and claimed recovery. |
| [Broker integration](../specifications/04-SimpleBroker_Integration.md) [SB-0.4] | Exact target/config, bounded operations, retained queue leases, cleanup order. |
| [Invariants](../specifications/07-System_Invariants.md) [IMMUT.1], [IMMUT.2], [IMMUT.3], [IMMUT.4], [OBS.6], [IMPL.8], [IMPL.9], [IMPL.10] | No lifecycle cache, runtime-state authority, worker broker effects, or second reactor. |
| [CLI](../specifications/10-CLI_Interface.md) [CLI-1.1.1], [CLI-1.2.1], [CLI-1.2.2], [CLI-1.2.3] | Result/stream/claimed diagnostics and exit meanings. |
| [Django realtime](../specifications/13C-Using_Weft_With_Django.md) [DJ-12.1], [DJ-12.2] | Idle disconnect actively wakes observation; iterator advance/close remain on one owner thread. |
| [Python](../specifications/14-Python_API_Surfaces.md) [PY-1], [PY-2], [PY-3] | Inherited submission-facade custody, accepted handle, effective context, and common result/error behavior. |
| [Pipelines](../specifications/12-Pipeline_Composition_and_UX.md) [PL-5.2], [PL-5.3] | Lifecycle completion proof versus best-effort status snapshots. |

[Spec promotion and assessment](./2026-10-09-direct-result-observation-spec-plan.md)
is review history and source evidence, not the governing contract. The earlier
[burst plan](./2026-10-09-burst-result-observation-plan.md) is a partial approach;
this plan replaces its result-repair direction and deliberately inherits its
session-owned submission-facade behavior under [SB-0.4] and [PY-1], including
verification. No further facade redesign or upstream setup change is included.
The [discovery placeholder](./2026-10-09-task-specific-result-discovery-placeholder.md)
recorded the earlier deferral; slice 3 addressed that implementation concern.
The successor owns further discovery and settlement revisions. Historical
review and execution evidence below remains a record of this approach.

Comprehension questions: Where does the accepted TID come from, and which
submission shaping precedes its capture? Which facts describe immutable routes,
and which must be read fresh? Why can a persistent batch end without the task
ending? Which calls behind a realtime snapshot still read the shared log?

## Spec Baseline

Plan type: implementation of already promoted requirements. There is no
Proposed Spec Delta or new promotion slice. If implementation requires changing
those requirements, declare the deviation and obtain a separately reviewed
spec-revision slice before proceeding.

Diff base: `8085962af48c2ae38b8487fc449a4b11e0471170`. The following captured
SHA-256 values pin the four promoted result files and existing Django realtime
spec before this plan's non-normative backlinks. Backlinks change navigation
only, not requirements.

| Captured governing file | SHA-256 |
| --- | --- |
| `docs/specifications/04-SimpleBroker_Integration.md` | `ba6bf41a26e0abd456f0188654a2dbc7670382ad5d87f2cc7b0812cc3161321c` |
| `docs/specifications/05-Message_Flow_and_State.md` | `6a56f4ed6b3e81cc78e15c9888ae123fc42d9311a0a1d4ac7678d3ced0dd8976` |
| `docs/specifications/10-CLI_Interface.md` | `0697817f28aa4580cae9ba1499e47701d0f5945ccf7eed1231d1765476bbbb98` |
| `docs/specifications/13C-Using_Weft_With_Django.md` | `4122bcd93665d896e5f5c6dbf004b13bc5f1c97850e1140fcc23d1cf242cfc21` |
| `docs/specifications/14-Python_API_Surfaces.md` | `8fc5e53d08c4fdba0d1d1f167cb2be5234dc978b7a305fb30cd59338c012671b` |

Other cited specs are at the diff base. Prior source/test candidates are inputs
to audit and preserve, not evidence that these requirements are implemented.
Use explicit file-list diffs/staging for each authorized slice. No commit, push,
CI/config/dependency change, or sibling-repo write is authorized by this plan.

## Preimplementation Structure and Planned Shared Owners

| Concern | Current owner and planned disposition |
| --- | --- |
| Accepted specification and TID | `weft/core/spawn_requests.py::submit_spawn_request` shapes the transport, then returns the broker-assigned ID. `weft/commands/submission.py::_SubmittedPreparedOutcome` retains only receipt/context; `weft/client/_task.py::Task` and `weft/commands/run.py::_LiveRunSession` lose configuration. Capture the final shaped configuration at this existing write boundary. |
| Application result capability | `weft/commands/result.py::await_task_result` always enters materialization. One-shot collection lives in `weft/commands/_result_wait.py`; persistent collection duplicates common machinery in `result.py`. Put common collection ownership in `_result_wait.py`, with thin callers and explicit mode-specific work-boundary rules. |
| Queue observation | `weft/core/queue_wait.py::QueueChangeMonitor` already wraps native activity waits and constructor-time watcher fallback. Extend it; do not replace it with a task subclass or new watcher framework. |
| Task-history acquisition | Core `weft/core/task_evidence.py` also reads task history, so commands cannot own the shared filtered primitive. Add it to this existing core owner; use it from `_task_history.py`, `_streaming.py`, realtime, full-TID snapshot collection, and core evidence. Keep `_task_snapshot_reducer.py` pure. |
| Hidden result-path reads | Realtime `_task_snapshot_event` enters `tasks.py::_task_snapshot`, pipeline configuration lookup, `system.py::collect_known_tid_snapshot`, and public config projection. Claimed-result preflight enters core `known_tid_evidence`. Pass retained configuration and acquired target events through these point-TID helpers; do not leave secondary full-log readers behind. |
| Monitor fallback | `weft/core/monitor/store.py::open_monitor_store(...).get_task()` and `tasks.py::_monitor_store_task_snapshot` own the existing read-only lookup/diagnostic meaning. Share the record lookup rather than reconstruct routes from the snapshot, which drops `spec`/`io`. |
| Error translation | `weft/commands/_boundary.py` owns eager/lazy typed error translation. Reuse it at the shared capability, including Python and realtime paths; CLI retains presentation and exit translation. Core reports operational `BrokerError`, never command-layer types or raw usage-like errors. |

## Design and Constraints

Use plain functions and context managers in the existing modules. No new task
subclass, routing hierarchy, store, metadata queue, background result service,
result relay, dependency, config key, or public CLI/API field is needed. Retain a
private resolved TaskSpec copy through existing internal outcomes/handles;
existing callers constructing a bare `Task(client, tid)` remain supported.

The submission implementation must have one preparation/write path. Keep the
integer-returning entry point as a thin projection over one private core
implementation returning `(committed_tid, shaped_transport_payload)`. Core
returns the payload that was actually serialized, not a bound TaskSpec. In
commands, record `task_tid` immediately after the successful write, finish the
connection operation and availability recovery, then bind that shaped payload
to the TID through one private helper in `submission.py` using the existing
TaskSpec resolver. This ordering preserves accepted-TID annotation if binding
fails. Both command submission and private run submission use that helper;
no repeated hooks or parallel preparation/enqueue path. On the run side,
`_enqueue_taskspec` returns the core pair, and `_run_with_managed_execution`
records `accepted_tid`, performs its existing recovery, then binds through the
shared helper inside its accepted-TID error annotation scope and calls
`on_submitted`. Do not reroute run through `_submit_prepared_outcome` or bind
inside `_enqueue_taskspec` before the TID is recorded. Extend
`_SubmittedPreparedOutcome` with the resolved accepted TaskSpec and carry it
through the private run callback into `_LiveRunSession`. Preserve bundle
provenance, explicit-ID behavior, and the authoritative broker-assigned TID.
Do not preallocate a replacement TID, reread spawn/history queues, or rebuild
from an earlier unshaped template. Accepted configuration excludes future
manager-authored parent fields and runtime state.

Preserve `Task`'s existing constructor, repr, equality and hash: use a private
`field(default=None, init=False, repr=False, compare=False)` for configuration.
Populate it once through a private construction helper before exposing the
frozen handle. Do not mutate it after construction. `_LiveRunSession` is an
ordinary private class, so its existing private constructor can accept and
retain the same configuration without dataclass machinery.

Retained configuration governs routes, collection mode, streaming behavior,
and submitted pipeline routing inputs only. It is not current task state.
Continue folding every matching event's fresh state/metadata and emitting those
events. Public snapshots obtain error, return code and lifecycle timestamps
from the logged TaskSpec carried by `CollectedTaskSnapshot.taskspec_payload`
and fresh acquired evidence. Pipeline status-queue lookup may use accepted
configuration; started/completed times must come from fresh evidence. Do not
replace fresh projection fields with submission-time state, or rebind known
routes each time a new TaskSpec event arrives.

Shared code owns queue acquisition, task-event acquisition, terminal
classification, grace/deadline calculation, final-drain decisions, and public
output assembly. Move the persistent result loop into the existing collection
owner and remove the duplicate implementation. Realtime keeps its distinct
non-consuming iteration/presentation, and interactive run keeps input and
STOP/ACK handling; both call common policy functions rather than restating
them. Share any operation state that those functions need in one private plain
state value; do not build a generic reducer/strategy framework. A small private
dataclass is justified only by the actual shared clocks/evidence, not by a
future extension. Reuse existing result/evidence types for returned values.

Keep task-log observation for task kinds whose completion protocol still needs
it. Do not optimistically omit it: persistent boundaries and pipeline terminal
proof need matching lifecycle events. A later omission for another kind needs
firing proof of all success/failure/timeout/cancellation/crash publications.
Known configuration must allow observation immediately, without a log TaskSpec
or queue-existence gate; acquired sufficient local proof must not wait for a
history initialization event.

### Complete filtered discovery

Add `read_task_history` in `weft/core/task_evidence.py`, returning matched
`(payload, message_id)` rows and the successfully inspected upper horizon. It
accepts an optional owner-borrowed broker and previous inspected horizon. The
existing command helpers become thin adapters to it; no second TID filter or
JSON decode loop remains on these point-TID paths. One core reader owns this
algorithm for pending task-log rows:

1. Register observation before inspecting durable contents. Capture committed
   upper horizon `H` with public `latest_pending_timestamp()`; it fetches only
   an ID. The first read has no lower bound, including older imported history.
2. Use public `find_message_ids()` with two literal candidate needles: the
   canonical full TID, and `r"\u003"`. Every JSON-escaped ASCII decimal digit
   has that prefix, so the second lane covers partially/fully escaped TIDs and
   escaped property names without enumerating encodings or adding a parser.
3. Paginate each lane independently with strict `after_timestamp` and exclusive
   `before_timestamp=H+1`, at the supported broker page limit. Deduplicate IDs,
   sort the merged IDs numerically ascending, and fetch each candidate with
   public `broker.peek_one(WEFT_GLOBAL_LOG_QUEUE, exact_timestamp=id,
   with_timestamps=True, include_claimed=False)`. There is no batch-by-ID
   API: account for one exact read per candidate. Parse JSON and require exact
   top-level TID. Reject nested/substring matches and malformed/non-object
   candidates. Preserve ascending event order across the two search lanes.
4. Advance the observation's inspected cursor to `H` only after both lanes
   finish, including a negative read. Preserve the previous cursor when the
   queue is empty; never move backward. At the greatest valid signed 64-bit
   ID, `(1 << 63) - 1`, use `before_timestamp=None`; no legal ID exceeds it.
   Otherwise use `H+1`. Never send the overflowing value to the broker.

The escaped-digit lane is deliberately broader and can transfer unrelated
escape-heavy candidates. Ordinary serialized unrelated TIDs do not enter it.
This is the bounded encoding-compatibility cost, not a normal whole-log replay
fallback. Both backends can still search bodies server-side; do not describe
this as an indexed semantic TID lookup or as removal of all database work.
Missing capabilities must fail visibly with typed execution errors. Initial
point-TID status/evidence reads also lose their unsafe `tid-1` shortcut: their
first candidate search intentionally covers the full retained ID range to
include older imported history. That can mean two full server-side searches,
not a whole-body transfer to Weft. Measure and flag this backend cost.

SQLite's existing fallback `QueueWatcher` separately receives log bodies in
peek mode. The application reader eliminates Weft history replay/JSON decoding;
it does not eliminate those broker-watcher transfers. Measure fallback watcher
bytes separately and report them as an upstream limitation. PostgreSQL's native
activity listener does not use that fallback body-drain path.

A disappeared exact candidate is absent evidence, not permission to rescan the
whole log. Broker/read failures do not commit inspected progress. Generated
live writes follow the promoted dependency-floor ordering guarantee. Exact-ID
administrative backfill behind a cursor requires a fresh observation; ordinary
pipeline outbox moves are unaffected. Do not add overlap/rescan heuristics.

### Observation, deadlines, interruption, and failure

Use one shared private acquisition context manager in `_result_wait.py`, reusing
`ExitStack` and existing context sessions. Register exact output/control/log
interest before result/claimed/snapshot checks. For discovered custom routes,
register the replacement before checking its contents or closing old routes.
Unknown mode must not consume a default-outbox value as one-shot success.

Compute one absolute caller deadline at the capability boundary and carry it
through discovery and collection. Shared helpers calculate the nearest actual
caller, completion-grace, persistent-boundary, or established quiet-output
deadline. `timeout=0` performs a non-blocking fresh evaluation. Remove the
100ms result caps, 200ms realtime startup budget, 50ms realtime grace probes,
and `effective_result_surface_wait_interval` once the wake/error paths work.
No timeout slice may force a new durable observation turn.

For either a native waiter or a fallback watcher that fails after startup,
choose the smaller MF-5 option: explicit failure. Record the cause, wake the
owning waiter, and raise an operational `BrokerError` chained from it. The
command boundary translates that into `CommandExecutionError`; core must not
raise command-layer exceptions or propagate raw `ValueError`/`TypeError` as
caller usage errors. Constructor-time fallback remains.

Run each existing fallback watcher in the same one-per-queue thread with a
monitor-owned target calling public `watcher.run_forever()`. Record an exception
or normal return while close has not been requested as observation failure,
and set the shared activity Event. Retain/join those thread references explicitly;
manual launch leaves upstream `run_in_thread` bookkeeping unused; public
`run_forever` still records its active run owner. Keep Weft's own thread
references rather than reading or writing upstream private fields. This
changes the target of the existing thread, adding no subclass or bridge thread.
Handle close racing before the target enters `run_forever()` so a late start
cannot clear a requested stop or leave an unjoined observer.
Check recorded failure before and after blocking. Close actively wakes an
in-flight wait, is idempotent, and closes each native waiter/watcher once.
Do not add retries, replacement threads, silent failure-to-poll fallback, or
per-caller failure-policy flags. Complete the non-result caller audit below
before changing the shared wait primitive; cap removal comes later.

A foreign `cancel_event` exposes only `is_set()` and cannot wake another
Event. Keep the existing optional argument, check it before blocking and after
an actual wake, and do not introduce a local timed check, bridge thread,
mutation of the caller's Event, or new token type. The caller's actual timeout
bounds an idle wait. With no timeout and no queue activity, merely setting that
foreign event cannot interrupt the blocked wait; state this limit in the
argument documentation and test it explicitly. A caller needing a bounded
idle wait supplies a finite timeout. Close and native failure must actively
wake the existing observation event. Do not claim prompt cancellation from an
`is_set()`-only interface, invent a cancellation deadline, or turn a caller's
timeout into repeated slices. Cancellation already set when a real wake or
deadline occurs is handled before reporting an ordinary timeout.


Shipped Django ASGI SSE and Channels streams need active cancellation without
a caller timeout. Dropping their old caps without replacing that wake would
retain disconnected workers/sessions indefinitely on an idle broker. Repair
this in slice 2 before removing caps. Reuse the existing `AsyncIteratorOwner`
and its cancellation Event/lock; do not add a token class or Event subclass.
It exposes `is_set()` for its existing flag and one private
`_register_wakeup(callback) -> unregister_callback` capability. Pass that
existing owner as the internal cancellation object through the current
`cancel_event: Any` argument. Public Task/RunSession signatures stay unchanged;
ordinary foreign flags remain supported with the limit above.

Add `QueueChangeMonitor.wake()` as the one shared non-blocking method that sets
its existing activity Event. Broker callbacks, failure and close use it too;
it remains harmless after close. The shared observation acquisition owner
recognizes the private callback capability and registers `monitor.wake` before its first cancellation check or
wait. `wake` sets the same activity Event as broker notifications and close.
The adapter owns cancellation intent; the monitor owns wakeup. Registration
runs under the adapter's existing lock, immediately wakes if cancellation is
already set, and returns idempotent deregistration for the observation scope.
Use per-registration callbacks so replacement routes can overlap safely.
Take callbacks under the lock but invoke them outside it. Add an explicit
paired-contract docstring marker at both `_register_wakeup` and the shared
observer registration site: deregistration is idempotent; callbacks are
non-blocking/thread-safe and perform no broker effects. Widen the internal SSE
`event_stream` cancellation annotation to the existing duck-typed input; it
must accept the owner as well as ordinary Event flags. Add no new protocol
class. Request-close sets
the flag and calls registered wakeups, then queues generator closure on its
existing single worker. Deregister before monitor disposal; a racing callback
may only set a harmless Event, never access broker resources. After wake, check
cancellation before durable reads, yields or timeout settlement.

Wire both SSE response close and Channels disconnect to the existing owner's
request-close path. Retain the owner on the Channels consumer once allocated;
disconnect before allocation sets its existing flag and registration later
sees it. Do not call `generator.close()` from the event-loop/request thread,
allocate another worker, or use timeout errors as disconnect control flow.
Test disconnect with an actually blocked idle Weft follow iterator, no caller
timeout and no queue publication. Assert owner advance unwinds, cleanup runs
on the same worker, session/watchers close, and there is no broker read on the
cancellation wake. Prove the actual monitor wait returns because the registered
wake fired, with no timed-out cap ticks before cancellation; merely observing
that closure eventually occurs would pass the old code. Repeat this test
after cap removal in slice 4 as a binding gate. Preserve existing primary/secondary cleanup error rules.
An in-flight backend operation can still outlive disconnect; this does not
promise cancellation of arbitrary I/O.

### Presence, settlement, and retained terminal evidence

Name one private policy owner, `_settle_result_observation` in `_result_wait.py`.
It receives collection mode, acquired evidence, output eligibility and the
original clocks. Thin consuming/non-consuming callers apply its decision;
state/evidence acquisition is separate from settlement. Existing terminal
classification/source selection in core remains the common authority. Write a
table-driven firing test for this literal policy, not tests of helper existence.
`D` is the caller's absolute deadline; `G` is the original applicable completion
or quiet-output grace deadline. After a real deadline/wake, acquire current
evidence and perform the eligible last non-blocking drain before deciding.
No new grace starts because another surface or terminal candidate appears.

| Mode / presence | Evidence and clock condition | Settlement |
| --- | --- | --- |
| Bare TID, no usable configuration, terminal proof, or readable eligible surface | `timeout=None`, first registered fresh evaluation finds none | Return `TaskResult(status="missing")`; `cmd_result` maps to `TaskNotFound`, CLI exit 2. Do not wait indefinitely for an unknown ID. |
| Same completely absent bare TID | `timeout=0` | Fresh evaluation, then `CommandTimeoutError`, CLI exit 124. |
| Same completely absent bare TID | Finite positive timeout | Observe missing configuration on actual activity until `D`, then observer timeout (124); no intermediate probe turns. |
| Accepted handle/session configuration, or discovered usable configuration | Queued, no task-owned initialization event | Enter direct collection immediately; never select `missing` solely for absent initialization. Wait to activity or real deadlines. |
| Bare TID queued before any usable configuration/evidence is visible | No timeout versus explicit finite timeout | Preserve the absence rules above. This slice adds no spawn/reserved-queue configuration reconstruction; an accepted handle is the immediate-wait path. Test this limitation explicitly. |
| Any mode | Competing task-log terminal proof, typed control proof, eligible final output, live evidence, terminal Monitor record | Apply MF-5 source order. Within control proof, task-owned terminal beats manager `wrapper_lost`; same-class latest wins. |
| Any mode | Selected task-owned failed/timeout/cancelled/killed proof, with or without output | Return that task outcome and error; output does not turn failure into success. A task `timeout` is not an observer `CommandTimeoutError`. |
| One-shot | Manager `wrapper_lost` plus final readable/emitted output, without stronger task-owned proof | Hold only the original terminal grace where time remains, then return failed with `wrapper_lost`; at `D` apply acquired terminal proof. Output does not outrank typed terminal control under MF-5. |
| One-shot | Selected completed proof plus eligible final output | Final non-blocking drain, then completed. |
| One-shot | Selected completed proof but no output; before `G` and `D` | Wait until the nearest actual deadline/activity. No second grace. |
| One-shot | Selected completed proof, no output, `G` expired | Last drain, then completed, including empty value if still absent. |
| One-shot | Selected completed proof, no output, `D` expires before `G`, including zero timeout | Last fresh drain; absent output means observer timeout (124). Do not invent success from an unexpired visibility grace. |
| One-shot | Eligible unambiguous final output alone, readable or already emitted | At the original quiet-output grace or caller deadline (including zero), complete; never report false timeout because the value was emitted rather than buffered. Streams/partial/ambiguous traffic do not qualify. |
| Persistent batch | Matched `work_item_completed`/`work_completed` boundary and output through that boundary | Complete this batch after its final bounded drain; the task may remain running. Do not consume the next batch. Emitted final values count with the boundary. |
| Persistent batch | No explicit boundary, non-streaming readable output | Preserve the existing permitted quiet-output grace and caller-deadline final batch drain. This completes a batch, not the task. |
| Persistent batch | Stream/emitted-only output with no work boundary or task terminal proof | Continue until actual activity/deadline; output alone does not settle the batch or prove termination. |
| Pipeline | Running/status/quiet or intermediate output, no pipeline lifecycle completion proof | Continue; neither quiet output nor best-effort status settles pipeline completion. |
| Pipeline | Pipeline lifecycle terminal proof | Apply its terminal status and common original grace/final-drain rules. |
| Any result mode | Actual claimed rows and no readable result, eligible recovery diagnostic | Return the existing claimed-result diagnostic. No claimed scan on empty turns. |

**Behavior difference to flag before runtime work:**
`test_await_one_shot_result_prefers_outbox_over_manager_wrapper_lost` currently
asserts completed, contrary to the common MF-5 priority. This plan follows the
promoted specification and changes that outcome to failed when no stronger
task-owned proof exists. The counterargument is substantive: final one-shot
output can be stronger practical success evidence than a manager inference.
Preserving that current override needs a separately reviewed spec decision;
do not silently choose it or change the test without recording the consequence.
The table above is the plan's explicit choice under the current approved spec.

After the initial filtered acquisition, consult the existing read-only Monitor
record once per observation when a full TID lacks needed configuration or
terminal proof and no stronger acquired proof already settles the result.
Borrow the owned queue, never initialize/migrate/write, and do not repeatedly
reopen the store on idle/activity turns. The store is not a new watched
completion surface; fresh live completion remains task-local/matched-log proof.
Share the record lookup and classifications with status consumers.

| Monitor lookup | Result meaning |
| --- | --- |
| Terminal-qualified record | Recover retained `spec`/`io` and terminal proof; apply the collector/table above at its MF-5 priority. |
| Uninitialized store, successful absent row, or nonterminal row | No configuration, live or terminal proof; use ordinary fresh-evidence/presence rules. Nonterminal record remains diagnostic history only. |
| Failed, incomplete or unsupported store, resolution depends on this fallback | Return `TaskResult(status="unknown", reconciliation={"classification": "monitor_store_unavailable", ...})` and the existing store diagnostic; CLI exit 1. Do not call it TaskNotFound or task failure. |
| Unavailable store with sufficient stronger acquired proof/configuration | Preserve the stronger result; optional fallback cannot downgrade it. |
| Bare configuration unresolved, store unavailable, no timeout or zero timeout | After fresh acquisition return unknown/exit 1. |
| Bare configuration unresolved, store unavailable, positive timeout before `D` | Remember the diagnostic and keep observing on actual activity until `D`. |
| Same unresolved observation at `D` | Unknown/exit 1 if fallback still prevents resolution; not missing/2 or observer timeout/124. Stronger acquired evidence replaces the diagnostic. |
| Known accepted/discovered configuration, ordinary live wait expires | Observer timeout/124; optional store trouble did not cause that expiry. |

If a bare TID needs fallback configuration and the store is unavailable:
with no timeout or zero timeout, return unknown/exit 1 after fresh acquisition;
with a positive timeout, remember that diagnostic and continue observing until
`D`. At `D`, if resolution still depends on the unavailable fallback, return
unknown/exit 1 rather than missing/exit 2 or observer timeout/124. New stronger
proof/configuration replaces that diagnostic. When accepted/discovered
configuration suffices and an ordinary live wait expires, return observer
timeout/124; optional store trouble is not the cause of that expiry.

The unavailable mapping uses existing fields and the established status
classification; it does not introduce a new status/error shape. Firing tests
must distinguish unavailable, absent and nonterminal cases from failed tasks.

### Shared monitor failure at non-result boundaries

Use one operational failure policy in `QueueChangeMonitor.wait`, with no
compatibility flag that leaves an observer dead. The implementation slice
must test these existing caller boundaries and add cleanup where needed:

| Existing caller | Required outcome on recorded observer failure |
| --- | --- |
| `manager_runtime.py` bounded startup/competing-launch observations | Abort the failed observation and preserve launch custody. Accepted-submission recovery keeps its existing `BrokerError` reconciliation/uncertain receipt behavior. |
| `manager_runtime.py` stop confirmation | Propagate operational failure; do not report confirmed stop or proceed to automatic force because observation failed. Public command translation reports execution failure. |
| `manager_runtime.py::start_manager` active launch observation | Invoke existing failed-start settlement/cleanup, then raise the existing manager-start error; never abandon an owned launch on the new exception. |
| `_spawn_submission.py` reconciliation wait | Operational failure flows through existing accepted-TID reconciliation/annotation, never usage or a fabricated absent submission. |
| `tasks.py` terminal/status and control-client waits | Propagate operational failure through the existing typed command boundary; no false terminal/ACK success. Bulk control retains per-target failure partitioning and existing all-failed `ControlRejected` behavior. |
| `queue.py` queue follow and `system.py` monitor follow | Close owned observation and raise typed execution failure. Preserve their advertised sampling intervals/broad inventory reads; no new fallback cadence. |
| `events.py` task-event follow/realtime/result waits | Same explicit command execution failure and owned stream cleanup. |

This can make a transient listener failure visible where capped polling used
to conceal a dead observer. That is an intentional repair of the shared
observation boundary. It is not permission to change task outcomes, accepted
submission custody, retry policy, or manager admission.

## Invariants, Hidden Couplings, and Rollback

TID allocation/immutability, forward-only state, frozen `spec`/`io`, reserved
policy, queue names, payload shapes, pipeline forwarding, and spawn process
boundaries stay intact. Runtime `weft.state.tasks.<tid>` remains best-effort
observability, is absent for queued children, lacks accepted routing, and is
not a replacement configuration source. Worker-to-owning-reactor delivery
stays intact; user code directly observing a child does not move broker effects
into Weft-owned worker lanes.

Persistent queue leases survive the operation, not the submitting client.
Connections/transactions and generator iterators end before waits or yields.
Watchers close before queues, and queues before the enclosing session. No
connections cross fork/spawn, and alternate effective contexts retain their
own exact target/config rather than using the client's original context.

There is no storage migration or one-way door. Existing producers remain wire
compatible. Implement and review slices in order; repair failure delivery before
removing caps. Roll back only explicitly owned slice edits against recorded
baselines, preserving earlier work and the promoted requirements. Cap removal
and its observation/error changes roll back together. Rollback may restore the
known performance defect; report that rather than changing the spec to fit it.

### Coordinated package release prerequisite

Weft and weft-django are separately versioned packages even in this repository.
Cap removal must not ship paired with an older adapter that cannot register a
wake. Repository owner owns the coordinated release: include the matching
weft-django version, update all root `weft-django` optional-extra floors and the
integration's compatible core floor in that owner-cut release, and document
in both changelogs that standalone weft-django installations must upgrade too.
An ordinary core-only upgrade does not force that optional adapter upgrade.
Do not call such a mixed installation verified or supported by this repair.

This is a shipping prerequisite, not permission to edit project configuration,
choose/publish versions, or touch CI in the runtime slices. Those owner release
changes require the repo's existing explicit authorization. Runtime validation
uses the two repo packages together; a release/config handoff must identify
the paired versions and floors before cap removal ships. Roll back the pair
together if the wake repair fails. No SimpleBroker dependency upgrade is needed.

## Ordered Implementation Slices

Every slice: read the named owner/tests/specs first, write a firing regression
before the repair, use complete type hints and top-level imports, add specific
`Spec:` backlinks, run its narrow proof, and obtain independent completed-work
review. Do not add new complexity suppressions as the default response.

1. **Retain accepted configuration: thin end-to-end child wait.**
   Files: `weft/core/spawn_requests.py`, `weft/commands/submission.py`,
   `weft/client/_client.py`, `weft/client/_task.py`, `weft/commands/run.py`,
   `weft/commands/result.py`, `weft/commands/events.py`; tests in existing
   client, submission, run-public,
   and result suites. Implement the one capture path described above, extend
   private submission outcomes/callback custody, and lend configuration to
   `await_task_result` and handle realtime observation. Keep public receipt,
   execution-result, and command signatures/shapes stable. Known-config calls
   bypass `_await_result_materialization`; one-shot/persistent/pipeline mode
   and explicit queues come from accepted values. Prove queued child output or
   failure with no task-owned TaskSpec event, custom routes, final shaping,
   authoritative TID, post-write binding failure retaining accepted TID,
   unchanged handle construction/repr/equality/hash, bundle provenance,
   alternate context, and client close.
   This slice proves no configuration discovery, not final filtered lifecycle
   I/O. Stop if it needs a new TID, public field, or parallel enqueue path.

2. **Share resource observation and deliver waiter failure.**
   Files: `weft/core/queue_wait.py`, `weft/_constants.py`,
   `weft/commands/_result_wait.py`, `weft/commands/result.py`,
   `weft/commands/events.py`, `integrations/weft_django/weft_django/realtime.py`,
   `integrations/weft_django/weft_django/sse.py`,
   `integrations/weft_django/weft_django/channels.py`, and named non-result
   callers only where needed
   for failure cleanup/translation; tests in queue-wait and observation-connection
   suites and representative manager/submission/control boundaries. Share
   route acquisition/cleanup, fix failed native/fallback waits and close
   wakeups, and check cancellation only at real observation boundaries.
   Register direct wake callbacks for the existing Django iterator owner and
   wire SSE/Channels disconnect before removing caps. Move startup snapshot
   and claimed checks after registration. Keep caps until slice 4 migrates all
   policy users. Prove absent queues, publication during registration, partial
   construction failure, native and SQLite fallback watcher failure without
   caller deadline, non-result outcomes in the failure table (including accepted
   submission survival, lazy error cleanup and active launch settlement),
   cancellation
   already set, cancellation on a real queue wake or caller deadline, an idle
   wait bounded only by the supplied timeout, direct Django idle-disconnect
   wake without a timeout/activity, registration/disconnect races, and iterator
   close without resource leaks. Stop if a background relay, bridge thread, local timed
   cancellation check, or timed durable probe appears.

3. **Use one filtered history and retained-terminal lookup.**
   Files: `weft/core/task_evidence.py`, `weft/commands/_task_history.py`,
   `weft/commands/_streaming.py`, `weft/commands/result.py`,
   `weft/commands/events.py`, `weft/commands/system.py`,
   `weft/commands/tasks.py`, `weft/commands/run.py`; tests in burst-result,
   result, realtime, retention-prune, and relevant evidence/snapshot suites.
   Implement the complete bounded reader above and use it at every point-TID
   call on result paths, including `known_tid_evidence`, `iter_task_events`,
   `follow_task_events`, realtime snapshots,
   pipeline config, public snapshot fields, and claimed preflight. Carry the
   reducer's existing `CollectedTaskSnapshot.taskspec_payload` rather than
   dropping it and rediscovering it. Preserve broad inventory scans for genuine
   list/all-task operations; do not change reducer purity. Share the read-only
   Monitor record lookup and diagnostic interpretation, retaining per-operation
   config and negative progress. Prove pagination beyond a page, false/nested
   matches, escaped/mixed TIDs/keys, older retained IDs, zero-match progress,
   concurrent append beyond `H`, disappearing rows, max-ID bounds, read errors,
   and retired custom routes with terminal/absent/unavailable/nonterminal store
   records. Stop on private SQL, upstream writes, silent truncation, or any
   normal full-body replay fallback.

4. **Unify collection policy and remove result probe clocks.**
   Files: `weft/commands/_result_wait.py`, `weft/commands/result.py`,
   `weft/commands/events.py`, `weft/commands/run.py`, `weft/cli/run.py`,
   `weft/commands/interactive.py` (retirement), `weft/_constants.py`;
   tests in result, realtime, run/interactive, Django realtime integration,
   and observation suites. Move
   persistent collection into the common result owner, share terminal/grace/
   deadline/output-assembly functions with non-consuming realtime. Include
   `iter_task_events`/`follow_task_events`: pass accepted configuration and
   filtered evidence through their final collection; remove their 100ms cap.
   Confirm all callers, then delete `execute_run(wait=True)` and its unreachable
   `_wait_for_task_completion`, `_InteractiveRunLifecycle`, interactive prompt/
   piped/session helpers, and inline/spec/pipeline completion callbacks. Retire
   their sole-use `weft/commands/interactive.py::InteractiveStreamClient` module
   and `tests/commands/test_interactive_client.py` after relocating still-required
   proof into active command/CLI suites. Remove its import/monkeypatch from the
   shared observation suite; move
   `test_interactive_log_acquisition_failure_closes_client` to the active
   acquisition owner while preserving primary/secondary cleanup-error proof.
   Update old owner mappings. Do not delete the shared observation suite. Do not
   resurrect its consuming output watcher as a second collector for STOP/ACK.
   File removal occurs only in the explicitly approved implementation slice;
   no files are deleted during plan authoring. Retain
   `execute_run` submission preparation/recovery and `session_wait_requested`
   persistent-wait validation. Move still-relevant private-branch tests onto
   `cmd_run`/`RunSession`; delete only superseded assertions, not product proof.
   The active CLI `:quit`/`:exit` path currently lacks required CLI-1.1.1
   STOP/ACK-budget-then-KILL behavior. Move that existing policy to one private
   command helper for `_LiveRunSession`, called by `cli/run.py`, using common
   watched control/terminal evidence, existing keyed ACK correlation and actual
   named deadlines. Keep ordinary
   public `RunSession.stop()` semantics intact. Prove ACK, terminal-before-ACK,
   budget expiry/KILL, and cleanup on the active CLI path before removing the
   dead owner. Update that spec's implementation mapping with the live owner.
   This is existing contract repair, not a new exit protocol or result relay.
   Resolve wrapper-lost
   and emitted-output differences with the explicit settlement table, not
   copying whichever branch currently passes. Remove obsolete materialization
   gates and capped waits, keeping bare-TID missing-config discovery in the
   same activity-driven observation. Apply one absolute caller budget and one
   completion grace; perform final drains before settlement. Normalize errors
   at the shared capability using `_boundary.py`. Prove mode distinctions,
   terminal source competition, streams/emitted-only values, delayed/empty
   final output, pipeline running output versus terminal proof, short/zero/no
   deadlines, and Python/CLI error parity. Stop if common policy is reimplemented
   in another mode or wrapper, or correctness depends on a periodic result turn.

5. **Close coverage, docs, and operational proof.**
   Files: the touched owners/tests, this plan, source-spec implementation notes
   and mappings, `docs/plans/README.md`; lessons only for a discovered durable
   correction. Revisit old tests that codify dead-observer success, global-row
   decoding, or pipeline output as task completion using the new contract,
   not weakened expectations. Run the final gates below; reconcile every
   mapping/backlink with actual code and truthful partial/completed notes.
   Conduct a real isolated fanout using WeftTestHarness, with children gated
   before initialization and unrelated activity present. Count history-body
   transfer/decodes and retained resource use. Report broker fallback watcher
   bytes separately; do not claim zero total SQLite observer body transfer.
   Demonstrate direct result/failure delivery without Weft configuration replay
   or idle application probes. Latency
   improvement is supporting evidence, not the pass criterion. Independent
   final review must include shared-code ownership and all assessed hidden
   reads. Stop if any claimed requirement lacks firing proof or required
   PostgreSQL/traceability verification is unavailable.

## Testing and Verification

Use `broker_env` real queues for acquisition/history and `WeftTestHarness` for
real client/CLI/lifecycle paths; preserve harness cleanup in `finally`.
Generate normal terminal fixtures through production task paths. Handwritten
malformed/escaped/imported rows are boundary-input tests, not fake lifecycle
proof. Use explicit handshakes to establish publication races and final output
visibility; controlled monotonic time may prove grace/deadline policy. No sleep
is the assertion. Instrument the actual fetch/decode boundary without replacing
its broker calls. Native waiter failure injection is allowed at that real
external failure boundary, not by replacing the collector under test.

Each listed contract case above must fire; prefer parametrizing existing tests
and reuse fixture setup. A new test needs a distinct credible regression not
already caught. The existing queued-realtime test must become green for the
right reason; the existing raw-cursor test should now prove no ordinary
unrelated application-reader body transfer, not merely one decode per row. Prove that an idle
wait uses the actual caller deadline with no intermediate cancellation ticks
or durable reads. Existence of shared
helpers alone is not behavioral coverage; also review the removed duplicate
owners/call sites.

Per-slice commands (run the files relevant to that slice, not all after every
edit):

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/core/test_client.py tests/commands/test_submission.py tests/commands/test_run_public.py -q
./.venv/bin/python -m pytest tests/core/test_queue_wait.py tests/commands/test_result_observation_connections.py tests/commands/test_event_observation_connections.py -q
./.venv/bin/python -m pytest tests/commands/test_burst_result_wait.py tests/commands/test_result.py tests/commands/test_realtime_events.py tests/commands/test_retention_prune.py -q
./.venv/bin/python -m pytest tests/commands/test_run.py tests/cli/test_cli_run.py tests/commands/test_observation_connections.py -q
./.venv/bin/python -m pytest integrations/weft_django/tests/test_weft_django.py -q
```

Run relevant shared cases through the existing `bin/pytest-pg` wrapper, which
owns isolated PostgreSQL setup/configuration and cleanup. Inspect its target
and marker behavior plus `tests/helpers/test_backend.py` first. For example:

```bash
. ./.envrc
./bin/pytest-pg tests/core/test_queue_wait.py tests/commands/test_burst_result_wait.py tests/commands/test_result.py tests/commands/test_realtime_events.py integrations/weft_django/tests/test_weft_django.py
```

Do not hand-build ambient backend variables/DSNs as a substitute for the repo
wrapper. Never use a production broker, disclose a DSN, or claim PostgreSQL
proof from skipped tests.

Final runtime gates:

```bash
. ./.envrc
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q
git diff --check
```

Apply adversarial acceptance floors through shipped CLI: correct 0/1/2/124
classes, no traceback for missing/invalid TID and observer failure, honest
pipeline/result status, malformed task containment, and advertised run/result
smoke. Reuse existing acceptance tests where they already fire.

Traceability: reconcile canonical spec sections, this plan, and owning code
backlinks in each slice. The required full strict tool was rediscovered:

```bash
/Users/van/Developer/backstitch/.venv/bin/backstitch check --repo-root /Users/van/Developer/weft --spec-root docs/specifications --code-root weft --code-root tests --code-root bin --code-root integrations --code-root extensions --test-root tests --test-root integrations/weft_django/tests --plan-root docs/plans --warnings-as-errors --format json --output /tmp/weft-backstitch-implementation-final.json
```

An unavailable or nonpassing required gate is a readiness blocker, not
permission to substitute a prose assertion. Narrow metadata/hygiene checks do
not establish complete traceability compliance. Compare normalized issues to
`/tmp/weft-backstitch-implementation-baseline.json`; zero introduced issues does
not waive existing debt.

Plan-only verification: metadata/hygiene, Markdown link and literal existing
path resolution, exact source-backlink checks, `git diff --check`, plus hashes
proving all earlier files outside the authoring scope are preserved. No runtime
suite is required to prove this planning artifact.

## Out of Scope and Separate SimpleBroker Action

No manager admission fix, schema-validation bypass, plugin-discovery cache,
SimpleBroker dependency upgrade, producer protocol change, new terminal envelope, monitor
retention extension, broader reactor refactor, or repository-wide polling repair.
The earlier polling audit remains evidence to triage separately; this plan
covers every polling site on the assessed result path and its shared owners.

Flag separately: session/core retention does not eliminate fresh-facade plugin
scans and schema validation. Public substring filtering limits candidate bodies
fetched and decoded by Weft. Backend search work and body transfer inside the
broker depend on SimpleBroker's implementation; this does not establish an
indexed semantic TID lookup. Upstream changes are not a hidden prerequisite
or an authorized sibling-repository change in this plan.

## Independent Review and Fresh-Eyes Review

Reviewers must read the governing promoted sections, current owner code, this
plan, and the assessment. Use authenticated local Claude as the independent
family, with read-only tools and no implementation/tests/delegation/commits.
Ask: Could an expert Python developer implement this confidently and correctly?
Is it simple, conceptually unified, elegant on Weft's principles, and free of
unnecessary abstraction or process? Is there still a duplicate common path,
missing wake source, incorrect completion rule, or hidden full-log read?
Return PASS/BLOCKED with evidence-backed findings, including proposed removals.
Resolve every finding explicitly and re-review material amendments. Repeat
independent work review after each meaningful runtime slice and before closure.

Author fresh-eyes review: completed after draft. Found and tightened two
ambiguities: the filtered owner must be in core because core evidence also
uses it, and accepted configuration must come from the final shaped spawn
write. A subsequent user correction removed local cancellation checks:
`is_set()`-only events cannot wake an idle wait, so actual caller timeouts
bound it and the limitation is explicit. Named the private accepted-spawn
outcome path and target-history reader; kept shared policies in existing
modules. Residual risks are the broader escaped-digit candidate lane, large
mode-specific collector migration, and backend verification. Independent reviews are recorded below; the final direct-wake amendment
passed Claude verification.
Claude round 1: BLOCKED (F1 unknown-TID settlement; F2 fallback-watcher death).
The findings/dispositions below retain that verdict. Round 2 verified its
fixes and identified the Django regression; round 3 passed the direct-wake
repair and N2–N4 clarifications.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

No implementation deviations declared. Slices 1–4 are implemented in the
uncommitted worktree and have completed full-reading independent PASS reviews.
Slice 5 operational probes, full runtime suites, canonical mypy/Ruff, architecture
and documentation checks pass in the uncommitted worktree. Final independent
Claude review passes implementation quality/scope. Strict traceability remains
nonpassing baseline debt; release prerequisites and separate owner decisions
remain open. Earlier PostgreSQL worker exits are not causally diagnosed. The
plan stays draft until landing is separately authorized and verified.

## Review and Execution Evidence

| Step | Evidence |
| --- | --- |
| Source audit | Root plus three read-only agents checked accepted transport/TID custody, point-TID hidden reads, public broker filtering/horizon semantics, existing wait primitive, cancellation compatibility, and Monitor retained fields. |
| Planning checks | Six metadata/hygiene cases passed. Plan links and reciprocal backlinks resolve; removing only the inserted backlink text reproduces all four governing baseline hashes. All 17 earlier files outside this authoring scope retain their captured hashes. `git diff --check` passed. |
| Author fresh-eyes | Separate post-draft pass tightened shared core history ownership, accepted-spawn custody, cancellation semantics, and exact invariant references. No runtime edits. |
| User amendment | Removed the proposed local timed cancellation check. Waits use actual activity/deadlines; an `is_set()`-only flag cannot wake an indefinite idle wait. No bridge thread or new API is introduced. |
| Claude round 1 | BLOCKED. Full read-only review found F1–F16; dispositions and source verification recorded below. No runtime edits or tests by reviewer. |
| Claude round 2 | FAIL. F1–F16 fixes verified; N1 found the shipped Django idle-disconnect regression. N2–N4 tighten run binding, retired interactive owner and fallback/deadline interaction. Reviewed baseline SHA-256 `563bc7f1334c274b5040f8081b6d32f5f68b7f77ad70ddb684bbd8a9b4ed8601`. |
| Claude round 3 | PASS, no blocker. Verified N1–N4 against source and judged direct wake simple/necessary, not a new cancellation architecture. Reviewed SHA-256 `adb72ffa00f36ce2506976b87dff288d39504779c86edbb6709d03f6a54b923b`. Non-blocking R1–R5 tightenings accepted below. |
| Final planning checks | After R1–R5 tightenings: six metadata/hygiene cases passed; 13 links and 54 existing paths resolve; five source specs differ only by navigation backlinks; all 17 earlier non-authoring files retain captured hashes; index status/count and `git diff --check` pass. No runtime verification claimed. |

## Independent Review Findings and Dispositions

Round 1 read current owners, promoted specs and source backends. Every finding
was verified against those sources or independently audited before disposition.

| ID | Disposition |
| --- | --- |
| F1 (P1) | Accepted. Literal absence/timeout table preserves default missing exit 2 and explicit-timeout exit 124. Accepted handles bypass absence; queued bare-TID limitation is explicit and tested, with no new spawn reconstruction. |
| F2 (P1) | Accepted. Existing fallback threads get monitor-owned targets that report exceptions/unexpected exit and wake the common waiter; both adapter modes have firing failure tests before cap removal. |
| F3 (P2) | Accepted with one shared failure policy. Operational `BrokerError` preserves accepted-submission recovery; the caller table names manager launch cleanup and public error translation. No silent-poll compatibility flag. |
| F4 (P2, human review) | Accepted. Named settlement owner and literal table. Flagged the current one-shot wrapper-lost/output discrepancy to the user; the promoted MF-5 priority governs unless separately revised. |
| F5 (P2) | Accepted. Accepted configuration controls routes/mode only; fresh logged TaskSpec/evidence owns lifecycle state and public timestamps/errors. |
| F6 (P2) | Accepted after repo-wide reachability audit. Delete unreachable private wait subtree instead of migrating it. First repair existing STOP/ACK/KILL contract on active CLI/session path; preserve generic stop semantics and public signatures. |
| F7 (P2) | Accepted. Task event follow paths share filtered acquisition/configuration and actual deadlines; unreachable interactive polling disappears with F6. |
| F8 (P2) | Accepted. Core returns committed ID plus shaped transport; commands record ID, finish broker/recovery operations, then bind once with accepted-ID annotation. |
| F9 (P2) | Accepted. Once-per-observation fallback trigger and terminal/absent/nonterminal/unavailable result mappings are explicit. No store polling or stronger-proof downgrade. |
| F10 (P3) | Accepted. Scope Weft replay counters separately from SQLite broker watcher body transfer; report the latter upstream. |
| F11 (P3) | Accepted for public frozen `Task`: private init/repr/compare-excluded field preserves constructor/equality/hash. `_LiveRunSession` is an ordinary private class, so the suggested second dataclass field is unnecessary. |
| F12 (P3) | Accepted. Slice 1 names `events.py` and all handle observation paths. |
| F13 (P3) | Accepted. PostgreSQL proof uses `bin/pytest-pg`. |
| F14 (P3) | Accepted. Core raises existing operational `BrokerError` with cause; command boundary translates. No new exception class. |
| F15 (P3) | No extra inline CLI backlink in planning: Related Plans already resolves bidirectionally. Runtime slice 4 must update CLI-1.1.1/1.2 owning mappings with live code; adding another navigation-only line now supplies no correctness. |
| F16 (P3) | Accepted. Sort merged IDs; use exact public borrowed-broker peek; handle max ID with no upper bound; explicitly account for first full-range server-side searches. |

User correction: reject the proposed local timed cancellation check. Actual
caller timeout bounds an idle wait; plain `is_set()` cannot actively wake it.
This supersedes round 1's favorable comment about the earlier check. Scoped
verification must inspect the amendment rather than treat that comment as
approval of the revised behavior.

## Round 2 Findings and Direct-Wake Amendment

| ID | Disposition |
| --- | --- |
| N1 (P1) | Accepted regression; rejected as a tradeoff. DJ-12.1/12.2 remain intact. Add Django owners/tests and direct wake registration on existing AsyncIteratorOwner, preserving worker-thread cleanup. No timer, subclass, token class, bridge thread or public signature change. This fills MF-5 interruption using the existing monitor Event; no normative spec change or disconnected-worker retention is proposed. |
| N2 (P2) | Accepted. Explicit run-side pair/capture/recovery/bind/callback sequence; do not change its session/recovery path. |
| N3 (P3) | Accepted. Retire unused InteractiveStreamClient with the dead waiting subtree after moving required policy/tests to the active common path. Do not add another consuming collector. |
| N4 (P3) | Accepted. Explicit unavailable-store versus bare finite-deadline priority; remember diagnostic until deadline, replace with stronger evidence, and distinguish normal known-config wait timeout. |

Separate author fresh-eyes pass on this amendment: confirmed Django owns the
cancellation intent and already has a lock/single-worker cleanup owner. A
callback only sets the existing monitor Event; it performs no broker effects
or cross-thread iterator close. Registration-before-check and idempotent
removal close the missed-wake/lifetime races. This necessary shared wake
mechanism replaces periodic checks without broadening the public API.
Claude round 3 independently verified this mechanism and returned PASS.

## Round 3 Non-Blocking Tightenings

| ID | Disposition |
| --- | --- |
| R1 (P2) | Accepted. Coordinated weft/weft-django release and floor/changelog owner action is a shipping prerequisite. Ordinary core-only upgrade with an old adapter is not covered. No release/config/CI edits are silently authorized in runtime slices. |
| R2 (P3) | Accepted. Explicit module/exclusive-test retirement, shared observation-case relocation, and active CLI test command replace the stale legacy test path. |
| R3 (P3) | Accepted. Slice-2 RED proof must distinguish direct wake from existing timed ticks; slice-4 rerun after cap removal is binding. |
| R4 (P3) | Accepted. Paired private-contract markers at both package edit points, idempotent deregistration/non-blocking no-broker callback semantics and widened internal SSE annotation; no new class. |
| R5 (nit) | Accepted. Explicit new shared `wake()` method, safe after close, and unavailable/deadline cases inside the table-driven policy cases. |

These notes tighten the mechanism already reviewed; no new runtime mechanism
or normative requirement is added. Final author check found no further
blockers. Runtime slices, their completed-work reviews, full verification and
the coordinated release gate remain future work.

## Runtime Execution Log

- 2026-10-09: runtime implementation authorized by the owner. Required context
  reloaded; existing dirty files captured in `/tmp/weft-result-implementation-start.json`
  before slice edits. Five runtime slices and their work reviews remain pending.

- Slice 1: the queued custom-route result/realtime and run regressions fired RED
  on actual timeout/prohibited configuration discovery; submission custody
  fired RED on missing configuration and uninvoked binding. After repair, all
  nine cases passed on isolated PostgreSQL through `bin/pytest-pg`. The seven
  submission/client/run suites passed on SQLite; mypy over 164 source/test
  modules and touched-file Ruff passed.
- Slice 1 Claude review: BLOCKED solely on missing run-side binding-failure
  proof (F1). No introduced runtime defect identified. F1 is being added.
  F2 accepted: remove unused configuration argument from raw event iteration
  until slice 4 uses it. F3 pipeline collection proof is assigned to slice 4;
  F4 retained-config/fresh-snapshot loans are slice 3; F5 effective context
  remains the retained runtime context, never inferred from accepted spec.
  N1 naming churn declined; N2 inaccurate defensive coverage marker removed
  with F1. O1 extra one-time validation cost will be measured in slice 5.
- Broader observer suites: two failures recorded, not waived. Existing queued
  bare-TID replay reads unrelated history (slice 3). The custom-route test
  replaces already-resolved immutable `io`; preserve its route-registration
  coverage by making the custom spec its first discovered configuration.
- Traceability checker rediscovered at the sibling Backstitch checkout. An
  explicit-root baseline reports 31 errors and 1171 warnings, primarily
  existing corpus/classification debt. Runtime closeout must distinguish
  introduced issues from that baseline and cannot claim the zero-debt gate.

- Slice 1 closure: Claude returned PASS in
  `/tmp/weft-result-slice1-review-closure.txt`. F1 now fires on omitted binding,
  pre-recovery binding, premature callback and lost accepted-TID annotation;
  F2's unused argument is removed. N3's branches predate this slice and stay.
  O2's accepted `Task.follow` handoff proof belongs to slice 4.
- Exact slice 1 commands (after `. ./.envrc`):
  `.venv/bin/python -m pytest tests/commands/test_submission_custody.py tests/core/test_client.py tests/commands/test_run_public.py tests/commands/test_submission.py tests/core/test_spawn_requests.py tests/core/test_ops_shared.py tests/commands/test_run.py -q`
  passed 287 cases; `./bin/pytest-pg tests/commands/test_submission_custody.py tests/core/test_client.py::test_accepted_handle_observes_custom_routes_before_initialization_after_close tests/commands/test_run_public.py::test_run_session_collects_custom_output_without_child_initialization`
  passed nine cases without skips. After F1/F2,
  `.venv/bin/python -m pytest tests/commands/test_run.py tests/commands/test_run_public.py tests/core/test_ops_shared.py tests/commands/test_submission_custody.py -q`
  passed 126 cases.
- Sensitivity controls, restored byte-for-byte before candidate runs: replace
  only run's `accepted_taskspec = _bind_accepted_taskspec(shaped_payload, tid_int)`
  assignment with `accepted_taskspec = None`. Run
  `.venv/bin/python -m pytest tests/commands/test_run.py::test_run_binding_failure_keeps_committed_tid_after_connection_and_recovery -q`:
  the control fails `DID NOT RAISE`; the restored candidate passes. Replace
  the implicit core return's shaped payload with
  `encode_taskspec_transport_payload(taskspec)` while retaining the actual write;
  `.venv/bin/python -m pytest tests/core/test_spawn_requests.py::test_retained_spawn_transport_matches_serialized_authority_shaping -q`
  fails both public/internal cases at the retained-versus-serialized payload
  comparison; the exact restored candidate passes both. Full command/mutation
  records live in `/tmp/weft_spawn_custody_sensitivity.py` and
  `/tmp/weft-slice1-core-sensitivity.txt`.

- Slice 2: shared `result_observation` owns registration-before-read, acquired
  queues/session and direct cancellation callbacks. Existing QueueChangeMonitor
  owns fallback public run/thread custody, latches operational faults, wakes
  on close and raises BrokerError with original cause. Existing Django iterator
  owner registers wake callbacks and closes on its single worker. No runtime
  subclass, cancellation thread, new persistence or public signature added.
  Startup now aborts unconfirmed launch on registration/wait failure, including
  constructor OSError/RuntimeError, and preserves a SUCCESS handoff. Stop and
  accepted-submission observers retain their existing failure/uncertainty policy.
- Slice 2 sensitivity: `.venv/bin/python /tmp/weft_slice2_sensitivity.py` runs
  five exact owner controls, each saved/restored byte-for-byte and checked with
  SHA-256, then reruns the same candidate nodes. Omit public watcher stop; omit
  `_raise_failure`; omit Django callback registration; misreport stop observation
  failure as confirmed; rethrow accepted reconciliation instead of uncertainty.
  Each control exits 1 and restored candidate exits 0. Exact commands/mutations
  and output are in `/tmp/weft-slice2-*-control.txt`, candidate counterparts and
  `/tmp/weft-slice2-sensitivity.txt`. The deferred fallback case proves public
  stop/join custody, not the separate pre-entry guard. Stop proof uses an
  unresolved live PID; it does not claim kill-eligible process coverage.
- Slice 2 test maintenance: old constructor fakes now patch the shared owner.
  Fallback connection warmup follows actual monitor construction/public watcher
  run instead of retired `run_in_thread` invocation. Injected partial-constructor
  fault obtains one real connection before raising, preserving cleanup proof.
  Error tests now assert PY-2 typed execution/usage error with original cause
  rather than a raw internal exception; their cleanup/custody oracles remain.
- Slice 2 type verification: canonical CI mypy command passed 444 files. Django
  integration test type check also passed with MYPYPATH pointing to its actual
  fixture/package roots. Earlier targeted invocation's three missing-import
  errors were caused by omitting those fixture roots; no suppression/config
  change was made. Touched-file Ruff and `git diff --check` passed.
- Slice 2 final focused SQLite command (after `. ./.envrc`):
  `.venv/bin/python -m pytest tests/core/test_queue_wait.py tests/commands/test_observation_connections.py tests/commands/test_observer_command_failures.py tests/commands/test_queue.py tests/core/test_manager_start_observer_failure.py tests/core/test_manager_runtime_connections.py tests/commands/test_run.py tests/commands/test_result_observation_connections.py tests/commands/test_result.py -q`
  passed, with five backend-specific skips. Result is in
  `/tmp/weft-slice2-combined-green.txt`. The full Django module passed within
  the broader run; that run also recorded the two history regressions and
  obsolete fixture-seam failures described above, rather than being called green.
- Slice 2 PostgreSQL command: `./bin/pytest-pg tests/core/test_queue_wait.py tests/commands/test_observation_connections.py tests/commands/test_observer_command_failures.py tests/core/test_manager_start_observer_failure.py tests/core/test_manager_runtime_connections.py::test_reconciliation_listener_failure_keeps_uncertain_accepted_receipt integrations/weft_django/tests/test_weft_django.py::test_async_iterator_owner_close_wakes_each_live_registration_outside_lock integrations/weft_django/tests/test_weft_django.py::test_async_iterator_owner_registration_observes_existing_cancellation integrations/weft_django/tests/test_weft_django.py::test_django_disconnect_directly_wakes_idle_weft_follow`
  passed 53 cases without skips in `/tmp/weft-slice2-pg.txt`. The wrapper
  accepts target nodes, not `-k`; the first rejected invocation ran no tests.
  Shared watcher resource/cancellation proofs thus ran on both backends;
  adapter probes must run again after slice 4 removes the caps.

- Slice 2 first Claude work review returned FAIL narrowly: no P0/P1 runtime
  defect, but missing deadline/cancellation proof exposed cancellation checked
  after timeout at completion-grace entry. `test_realtime_cancellation_precedes_coincident_caller_timeout`
  now covers grace-entry race and deadline wake using one controlled clock for
  both deadline and grace owners. The grace-entry case fired RED with an
  ordinary CommandTimeoutError; both candidate cases return quietly with no
  result read after cancellation. Exact command: `.venv/bin/python -m pytest
  tests/commands/test_event_observation_connections.py::test_realtime_cancellation_precedes_coincident_caller_timeout -n 0 -q`,
  recorded in `/tmp/weft-slice2-cancel-deadline-red.txt` and repair suite output.
- Review P3 overlapping fallback subscriptions accepted. Each realtime route
  set now owns a separate log Queue handle while borrowing the existing
  session. The outer command-resource scope is necessary for that additional
  handle and primary/secondary cleanup policy. A trial nested session exposed
  real SimpleBroker session-close protection during an active caller lease; it
  was corrected before slice closure. The full realtime suite is green.
  `/tmp/weft_slice2_route_sensitivity.py` changes only the watched-log loan to
  the caller's shared handle; a real caller write then fails StopException
  after old/new subscription closure. Byte-restored candidate passes. Exact
  command/mutation/hash/output is in `/tmp/weft-slice2-route-control.txt`,
  `-candidate.txt` and `-sensitivity.txt`. No upstream private slot inspection
  or modification was used by this test.
- Review simplifications accepted: remove redundant pre-entry watcher guard
  (public run_forever already honors stop), remove queue-watch unused initial
  monitor assignment, and document recorded-state/recheck wake semantics and
  repeated BrokerError/close outcomes. Event/realtime failure proof now asserts
  every owned queue closes. Old realtime fault fixtures patch the common
  constructor only during route acquisition, preserving their exact two-route
  monitor and partial-cleanup oracles.
- Sequencing clarification from review: actual idle deadline-only waits and
  the foreign-is_set-only limitation proof run in slice 4, after cap removal.
  Slice 2 deliberately retains the old caps; it does not claim those two
  proofs. Bare discovery-to-collection route continuity and the grace-loop
  deadline spin remain assigned to slice 4. This changes proof timing only,
  not the approved product behavior or implementation scope.
- Slice 2 closure verification: `.venv/bin/python -m pytest tests/commands/test_realtime_events.py tests/commands/test_observation_connections.py tests/commands/test_event_observation_connections.py::test_realtime_cancellation_precedes_coincident_caller_timeout integrations/weft_django/tests/test_weft_django.py -q` passed with four backend-specific skips.
  PostgreSQL repeated the prior 53-node command plus the new coincident-deadline
  node and full `tests/commands/test_realtime_events.py` (new overlap case is in
  the observation module): 78 passed, zero skips. Outputs are
  `/tmp/weft-slice2-closure-suite.txt` and `-closure-pg.txt`. Canonical mypy
  remains green over 444 files; touched-file Ruff and `git diff --check` pass.

- Slice 2 review bootstrap repair: the first repair PASS disclosed incomplete
  required agent reading. It was not accepted as gate closure. A fresh Claude
  review received the full required context in order and explicitly confirmed
  reading it before reaffirming PASS. Report: `/tmp/weft-result-slice2-bootstrap-closure.txt`.
  This closes only slice 2, not remaining implementation or shipping gates.

- Slice 3 candidate: one core public-API history reader now owns two complete
  candidate lanes, exact top-level identity, ordered bodies and negative progress.
  Command adapters and point-TID evidence/snapshot callers borrow its rows.
  Accepted routing remains separate from fresh logged state. Shared read-only
  Monitor lookup retains complete terminal-qualified records and diagnostics;
  nonterminal records do not supply result presence or routing.
- Slice 3 firing evidence: production-retired custom results failed before the
  repair in `/tmp/weft-slice3-retired-owner-red.txt`; diagnostic failed Monitor
  activity was wrongly terminal in `/tmp/weft-slice3-monitor-qualification-red.txt`;
  a submitted handle lost accepted custom routing in
  `/tmp/weft-slice3-handle-snapshot-red.txt`. Candidate repairs pass. Thirteen
  exact-owner sensitivity controls failed meaningfully and their byte-restored
  candidates passed, recorded in `/tmp/weft-slice3-sensitivity.txt` and the
  corresponding control/candidate files. Unsupported new keywords alone were
  contract checks, not regression RED proof.
- Slice 3 review preparation: realtime now acquires a fresh suffix after yielding
  output, since writers may commit while the iterator is suspended. The original
  borrowed suffix is not permission to skip that fresh acquisition. Existing
  immutable-route tests were corrected to test first discovery rather than
  changing accepted IO. Monitor fixtures now patch the common lookup owner;
  typed observer-failure assertions retain the original cause.
- Slice 3 PostgreSQL: `./bin/pytest-pg tests/core/test_task_history_reader.py
  tests/commands/test_task_history_adapters.py tests/commands/test_point_snapshot_loans.py
  tests/commands/test_retained_result_history.py tests/commands/test_realtime_events.py
  tests/commands/test_event_observation_connections.py` passed 67 cases with zero
  skips, output `/tmp/weft-slice3-final-pg.txt`. Canonical mypy passed over 448
  files. The final added closure passed 17 cases. Broader SQLite checks exercised 323 nodes: two existing snapshot stub signatures
  needed the new loan arguments, preserving their terminal/timestamp oracles.
  The repaired ops module passed all 11 cases in `/tmp/weft-slice3-ops-repair.txt`;
  nine PostgreSQL physical-connection cases skipped on SQLite. Full Ruff and
  `git diff --check` pass. Independent Claude review remains pending. No cap removal,
  full result-contract compliance, commit or shipping readiness is claimed.

- Slice 3 metadata closure: six plan/spec hygiene cases passed. The suppression
  inventory check found three required slice-2 Django test imports under that
  module's existing fixture-bootstrap E402 directive; the raw inventory now
  records 33 rather than 30. No suppression directive was added. The retired
  point-status BLE001 owner RUFF-SUP-355 is removed, raw count 135 to 134.
  Repeated metadata proof is recorded in `/tmp/weft-slice3-doc-repair.txt`.
- Slice 3 first Claude invocation timed out after its ten-minute budget without
  a verdict. It does not count as independent approval. A fresh narrower review
  has the same fully supplied required reading; runtime slice 4 remains gated.

- Slice 3 review closure audit: the retry returned PASS but explicitly disclosed
  missing writing-plans and adversarial-acceptance-probes reading. That verdict
  was not accepted as the slice gate. A new ordered context file contains all
  thirteen required files in full, including both omitted runbooks.
- Review F1 repaired: Task.terminal_snapshot now lends accepted configuration;
  its shared private owner registers queues, acquires history once per turn,
  retains the inspected cursor and Monitor lookup, lends rows to evidence/status,
  and waits only on actual activity or its caller deadline. Discovered custom
  routes register before inspection in the same acquired turn. Bare handles
  retain existing public TID normalization. The public adapter and private owner
  both retain typed operational-error translation.
- Review F2/F3 repaired under current MF-5: nonterminal raw history is diagnostic,
  not actual liveness proof. Qualified retained terminal evidence can recover
  routing and termination despite that residue. Fresh actual live runtime/PONG
  evidence suppresses it; stronger task log/control/eligible output proof wins.
  One shared monitor_terminal_evidence function owns qualification and fallback
  priority. Production Consumer finalization and Monitor collation tests retire
  actual terminal rows/runtime proofs while retaining nonterminal activity.
  The initial fixture error (unsupported Queue.clear) was corrected before the
  valid frozen RED run. All five original repair nodes then failed at their
  intended behavioral assertions in /tmp/weft-slice3-review-repair-valid-red.txt.
- Review F4 removed: realtime's impossible materialization fallback and payload
  guards no longer contain a second acquisition branch. F6 retains ordinary
  corrupt-store diagnostic fallback with the house pragma and reason; fatal
  BaseException is not swallowed.
- Review F5 retained deliberately: the three public-capability checks enforce
  the plan's explicit core BrokerError boundary when a backend lacks the
  required filtered APIs. Letting an absent/noncallable method fail unwrapped
  would violate that boundary and can classify an operational TypeError as
  usage elsewhere. The checks add no abstraction or fallback and do not wrap
  ordinary backend exceptions. This is implementation justification, not a
  change to the normative reader contract.
- Slice 3 review repairs: focused SQLite suite passes (nine PG-only connection
  cases skipped); canonical PostgreSQL wrapper runs all 48 cases with zero
  skips in /tmp/weft-slice3-review-repair-pg.txt. Broader SQLite regression ran
  281 cases with one obsolete status-helper patch seam failure; its context
  oracle was retained and moved to _task_status_record. The original terminal
  connection tests now trigger actual monitor wakes rather than a retired
  sleep seam and still check retained physical connections and cleanup.
  Full completed-reading independent review is still required before slice 4.

- Full-reading slice 3 repair review returned FAIL narrowly for N1: a claimed
  outbox diagnostic has terminal=True to end observation, but is still stale
  recovery evidence under MF-5. That flag cannot give it task-proof priority
  over a qualified retained terminal record. The review accepted F1/F4/F5/F6,
  and confirmed all thirteen required files read. It rejected unsupported
  partial-TaskSpec merging: Monitor summaries share the same redaction source.
  Report: /tmp/weft-result-slice3-repair-review.txt.
- N1 firing proof uses actual Consumer/Monitor publications, actual outbox
  read_one and positive claimed/zero unclaimed counts. All three status,
  terminal-snapshot and materialization nodes reported failed before repair
  (/tmp/weft-slice3-claimed-retention-red.txt). Shared Monitor priority now treats
  claimed_result_without_terminal as diagnostic; the result caller no longer
  bypasses that predicate with acquired.terminal. SQLite34 and PostgreSQL23
  pass with zero skips in the latter; canonical mypy449, full Ruff and diff
  whitespace checks pass. Outputs: /tmp/weft-slice3-claimed-retention-green.txt,
  -pg.txt and -mypy.txt. Final consuming claimed-result settlement remains
  slice 4; this proof covers selected evidence only.
- Terminal negative-progress sensitivity: resetting only the private terminal
  reader's after_timestamp to None fails its exact cursor assertion, restored
  bytes pass. Hash restoration checked, outputs in
  /tmp/weft-slice3-terminal-cursor-control.txt and -candidate.txt.

- Slice 3 accepted closure: fresh Claude confirms all thirteen required files
  read, NO_FINDINGS, REVIEW_STATUS completed and REVIEW_VERDICT PASS. Report
  /tmp/weft-result-slice3-n1-closure.txt. F1–F6 and N1 dispositions are closed.
  The reviewer withdrew the optional helper-extraction nit: status projection
  and result routing keep their genuine ownership while one predicate ranks
  retained proof. Slice 4 is released; slice 5 and shipping gates remain open.
- Slice 4 active-CLI constraints: explicit :quit/:exit uses the named STOP/ACK
  budget and KILL only on expiry. Keep EOF as stdin closure followed by ordinary
  result waiting, which permits legitimate work after EOF; do not invent an
  unapproved forced-termination deadline for that case. The EOF-ignoring child
  proof exercises explicit quit, not bounded EOF-alone. After successful exit
  observation, one existing INTERACTIVE_OUTPUT_DRAIN_TIMEOUT join is a genuine
  best-effort output drain deadline, followed by active cancellation and owner
  join; it cannot promise frames committed beyond that budget. On observation
  failure cancel immediately before join. No repeated timed result probe.

- Slice 4 initial firing proof: all six public activity/budget/cursor cases fail
  at the intended behavior on the pre-repair owners. Actual waits are 0.1 rather
  than the caller budget or None; follow starts a new wait after its original
  zero deadline; adjacent imported stream IDs are skipped. Output is
  /tmp/weft-slice4-activity-red.txt. Follow's injected fault was scoped to the
  observation so harness teardown uses the real owner; the repeated RED in
  /tmp/weft-slice4-follow-red.txt has no cleanup failure.
- Slice 4 Django binding RED uses actual supplied waits rather than forcing
  None in the test seam. Both SSE and Channels disconnect cases show [0.1]
  instead of [None] in /tmp/weft-slice4-django-red.txt. The direct wake itself
  remains real; this distinguishes removing the cap from eventual closure.

- Slice 4 consuming RED: actual Consumer emitted/null boundary cases pass as
  contract coverage; stale prior batch custody, pipeline running-output
  completion, original grace/deadline, and wrapper-lost output priority fail
  on their behavioral oracles in /tmp/weft-slice4-shared-owner-red.txt. The
  initial CLI fixture did not initialize a discoverable task before public
  STOP lookup and caused hung joins/daemon teardown; those timeout failures
  are not accepted evidence. The fixture was repaired to initialize a real
  Consumer, retain its real publications and own baseline teardown.
- Finite active CLI RED: /tmp/weft-slice4-active-cli-valid-red.txt shows three
  intended failures (unkeyed :quit/:exit STOP and failure without active output
  cancellation). Real stdin-reader EOF completion, stdout and stderr pass. No
  EOF regression is claimed. Runtime slice 4 production work is released.

- Slice 4 first candidate exposed consolidation import/test seams and active
  interactive fixture gaps. The canonical settlement/mode and filtered discovery
  cases passed; no overall PASS was claimed. Repairs moved hooks to live owners,
  kept the 600-frame prefix proof on production stream writers (chunk size one),
  and retained original batch observation clocks across paused `follow` yields.
- Realtime now takes a fresh last drain when local terminal proof first appears
  after stream peeking. This is triggered by new evidence, not a timed probe.
  The live EOF test also accounts for existing task shutdown deleting standard
  control queues unconditionally: its actual producer retains its drive scope
  until stderr is observed. Runtime-only control frames remain best effort at
  shutdown; this repair does not extend their retention or change producers.
- Consolidation retires five existing Ruff suppression groups (106, 108, 109,
  113, 332), with no new suppression. The live realtime exception remains and
  its evidence now points to retained real-task proof and adjacent imported IDs.
- The broader slice-4 run found a real persistent-task defect: an old empty
  work boundary could settle the next result call before its response arrived.
  The collector now distinguishes the initial historical boundaries from new
  boundaries. Historical boundaries may match unread output, but cannot prove
  a newly empty batch. The existing real continuous-CLI second-result proof
  passes with its value assertion intact.
- Slice-4 SQLite candidate gates passed: 129 narrow lifecycle/policy cases,
  121 repaired result/realtime/connection cases, and 229 final related cases.
  Canonical mypy passed across 450 source files; full Ruff, suppression index
  and diff checks passed. Seven settlement/grace controls failed at their
  intended assertions and the byte-restored candidates passed; evidence is
  `/tmp/weft-slice4-control-run.txt` and its named control/candidate logs.
- The initial PostgreSQL gate exposed a physical connection added at final
  output drainage. A temporary real-connect stack diagnostic established its
  source as first-use facade schema validation, not a delayed listener or route
  replacement. The assertion has not been weakened or moved. Repair and a
  fresh PostgreSQL gate are required before independent slice-4 acceptance.
- The repaired consuming path uses the initialized observation queue's broker
  for named control/output operations. It releases each operation before any
  yield and retains exact public claim/delete semantics. All six PostgreSQL
  connection-stability cases now pass without changing their oracle. The
  broader PostgreSQL run passed 124 cases and exposed two STOP-owner lookup
  loan failures: first-use validation occurred inside the borrowed lookup.
  That lookup will move into the first already-owned operation.
- A fresh active-CLI audit found two inherited cleanup gaps still reached by
  this slice: a drive failure skipped whole-session disposal, and raw finally
  disposal could replace a primary render/wait error. Use the existing resource
  scope around the complete CLI session lifetime and renderer; `cli/app.py`
  participates only to delegate drive/wait to that shared adapter owner.
  Firing failure/resource-priority proof is required before acceptance.
- CLI cleanup RED: four actual CLI acquisition-failure cases fail because
  the session remains open before fixture cleanup; the real renderer's close
  failure replaces its rendering error. The initial wait-cleanup fixture did
  not inject its fault into the constructor-captured input close callback and
  is rejected as firing evidence. After installing that actual close fault
  before session construction, it fires and replaces the acquisition error,
  bypassing typed CLI exit handling. Evidence is
  `/tmp/weft-slice4-cli-custody-red.txt` and
  `/tmp/weft-slice4-cli-custody-valid-red.txt`.
- Broader shipped-CLI coverage exposed two more real regressions, rather than
  accepting command-only coverage: a partial stream's own claimed rows were
  mistaken for pre-existing claimed-result blockage, and the stream adapter
  rendered encoded frames and then repeated the assembled value. The shared
  collector now tracks successful own claims independently of final-value
  presence. Structured frames use the same core codec with no print callback;
  CLI stream projection marks those frames emitted while ordinary follow
  retains its assembled value. No second collector is introduced.
- Preserve numeric absent-selector behavior (missing exit 2; explicit zero
  timeout exit 124) by registering runtime-state interest only for a canonical
  TID. Core runtime queue naming stays strict. Stream/control peek scans now
  bound fetching at their captured upper ID, including the max-ID case, rather
  than only filtering bodies after fetching an unbounded generator.
- CLI rendering will share the existing stream-text decoder and final-result
  exit renderer. Real task failure/timeout stream proof is required; a stream
  returning exit 0 merely because it ended is not accepted. The ongoing
  candidate evidence is `/tmp/weft-slice4-custody-sqlite.txt`; its eight failures
  are not waived. Four input-close identity probes also need their tracking
  hook installed before construction captures the actual callback; the session
  closure assertions already pass after the production repair.

- Final active CLI stream RED is real task failure and task-limit timeout:
  `/tmp/weft-slice4-stream-status-red.txt` returns 0 for actual failed/timeout
  publications instead of 1/124. Constructor-captured input-close tracking now
  passes with its name/thread oracle unchanged. Shared stream text decoding
  lives in `weft/core/outbox.py::decode_stream_text`; existing control,
  interactive and result renderers use it. CLI stream completion delegates
  to the existing result exit renderer and retains its no-newline framing.
  Suppression group 368 became unnecessary and is retired, with no new groups.
- Post-repair wider SQLite and PostgreSQL gates found fixture boundary errors,
  not accepted green runs. The task timeout now correctly returns 124 with the
  canonical “Target execution timed out” message; its test expected the joined
  word “timeout”. The PostgreSQL STOP loan now adds no connection, but its source
  expectation confused classification `terminal_monitor_store` with actual
  source `monitor_store`. Correct that source expectation and assert qualified
  terminal cancellation as well. Evidence: `/tmp/weft-slice4-custody-repaired-sqlite.txt`
  and `/tmp/weft-slice4-custody-repaired-pg.txt` (169 PG cases passed; two failed).
- The actual EOF-ignoring child proof sampled process liveness immediately after
  STOP settlement and exposed existing core termination semantics: shutdown
  consumes its stdin grace before issuing termination, and an exhausted deadline
  does not wait for OS reap. The proof must use one real bounded Popen exit wait
  before its unchanged dead-child assertion. This does not alter the producer
  termination/ACK contract or introduce result polling; terminal publication is
  not a promise of synchronous OS reaping. No core termination fix is claimed.

- Slice-4 closure candidate: affected SQLite suite passes with thirteen
  intentionally PostgreSQL-only cases skipped, which are covered by isolated
  PostgreSQL. The final PG gate passes 323 cases with zero skips in
  `/tmp/weft-slice4-accepted-candidate-pg.txt`. A transient large-prefix worker
  crash happened during the preceding failure-stopped run; its exact cause is
  not established. The unchanged 600-frame proof and explicit retained-source
  STOP cases passed the isolated three-case gate, then the whole PG gate passed.
  No retry/sleep/oracle relaxation was added to the stream proof.
- Final slice-4 mypy passes 450 files; Ruff, suppression index, diff check and
  51 spec/plan/seam/suppression policy cases pass. Evidence is the named
  `/tmp/weft-slice4-final-closure-*` files and
  `/tmp/weft-slice4-doc-policy-gates.txt`. The STOP source fixture now deliberately
  retires both ephemeral controls and runtime state so live retained log versus
  retained Monitor proof is deterministic. Independent acceptance still pending.

- Independent full-context Claude slice-4 review returned FAIL, despite the
  candidate gates above. Its four blocking findings are unchanged claimed
  residue causing a realtime hot loop (F1), an expired persistent quiet grace
  causing zero-time waits after competing claims (F2), live stderr rendered on
  stdout (F3), and unavailable Monitor diagnostics escaping before the caller
  deadline (F4). Source evidence is `/tmp/weft-result-slice4-review.txt`.
  Non-blocking findings N1–N4 cover obsolete parallel helpers, duplicated final
  event projection, stream traffic incorrectly used as completion proof, and
  a broker iterator retained across realtime yields. All require disposition
  before a new independent acceptance; the private fanout probe remains gated.
- Firing repair REDs: `/tmp/weft-slice4-review-blockers-red.txt` demonstrates
  F2, F4 and noninteractive F3 using real Consumer/CLI publication. Its
  interactive case exposes an additional live-prefix delay, not a second F3
  proof. `/tmp/weft-slice4-realtime-claimed-stream-red.txt` demonstrates F1 and
  consuming stream-only false completion; read-only realtime already rejects
  that stream-only fallback. `/tmp/weft-slice4-broker-iterator-red.txt` uses
  actual command streaming and observes one active broker iterator at the
  realtime stdout yield. The earlier function-return-only version produced no
  stdout frame and is rejected as N4 firing evidence.
- Repairs preserve the approved MF-5 contract: stream origin is retained in
  the existing shared decoder and excludes streamed values from one-shot
  completion fallback. Frames are still delivered live. Final event projection
  is shared, semantic terminal identity excludes fresh diagnostic clocks, and
  realtime rows are copied through the existing bounded named-queue decoder
  before yielding. No new observer subclass, relay, polling timer, or queue
  implementation is added. Bare Task.result/await results retain their existing
  missing-result value contract; command/CLI availability translation remains
  at that adapter boundary.

- Focused repair gate passes the result/history adapter migrations and every
  review regression with three PostgreSQL-only connection cases skipped:
  `/tmp/weft-slice4-review-repairs-sqlite.txt`. Canonical type checking initially
  finds five new test annotation errors, not accepted as a green gate. Correct
  iterable/iterator ownership, close through the existing iterator scope, and
  explicitly type the two-batch completion predicate before rerunning.
- The old stale-boundary fixture incorrectly required leaving the first unread
  current stream frame untouched. MF-5 permits following unread output before
  its boundary. It now retains the observer-timeout oracle against old-boundary
  false completion and asserts actual live delivery/claim. The real early-final
  two-batch proof separately asserts that completing the first collected result
  leaves the next batch pending. This is an explicit contract-based oracle
  correction, not a claim that every consumed stream frame proves completion.
- N1 retirement removes synthetic initial-terminal adapters and compatibility
  mode/loaned-boundary branches after all real callers and useful tests move to
  the canonical owner. N2 final projection is shared while existing command
  availability translation remains at its original boundary. C901 compliance
  requires no new suppression: pending event projection uses `yield from`, and
  optional boundary observation time is assigned directly.

- Repair closure: affected SQLite passes with thirteen intentional PG-only
  skips; canonical mypy passes 450 files; full Ruff, suppression index, diff,
  and 51 metadata/spec/seam/suppression policy cases pass. Artifacts are
  `/tmp/weft-slice4-review-closure-{sqlite,mypy,ruff,policy,diff}.txt`.
  Obsolete active-CLI test history hooks moved off the removed streaming alias
  onto actual remaining history owners; post-cancel-read oracles stay intact.
- New live-prefix and first-claimed-ID controls each fail the real two-batch
  proof at their intended assertion and the byte/hash-restored candidate passes:
  `/tmp/weft-slice4-live-prefix-{control,candidate}.txt` and
  `/tmp/weft-slice4-batch-custody-{control,candidate}.txt`.
- PostgreSQL initially passes 406 cases but its 600-frame worker exits
  unexpectedly while a separate full SQLite gate also runs. That is not an
  accepted PG gate and repeats the earlier unexplained worker-exit signature.
  The same unchanged PG runner/targets, executed without concurrent SQLite,
  pass all 407 cases with zero skips in
  `/tmp/weft-slice4-review-pg-diagnostic.txt`. A private logging plugin preserves
  actual pytest timers and records starts, finishes and timer firing; no timer
  fires, and the 600-frame case completes in 18.737 seconds under its unchanged
  30-second marker. Evidence: `/tmp/weft-pg-timeout-observation.jsonl`.
  The previous exit's cause is not established. Resource contention triggering
  the thread timeout is plausible, not a proven diagnosis. No retry, per-test
  limit change, suppression or frame-oracle relaxation is introduced. Final
  backend test jobs will run separately with their required internal parallelism.
- Full strict Backstitch is available and has been run. The baseline has 31
  errors and 1171 warnings; the closure candidate has 31 errors and 1170
  warnings. Normalized issue comparison finds zero introduced issues after
  exact mappings and backlinks are synchronized. Existing traceability debt
  remains a readiness blocker; a narrow policy gate is not its substitute.
  Artifacts: `/tmp/weft-backstitch-implementation-{baseline,review-closure}.json`.

- Fresh full-reading slice-4 closure review returns FAIL in
  `/tmp/weft-result-slice4-closure-review.txt`. It verifies F1–F4 and N1–N4
  are fixed, but identifies new R1: non-rendering callers can claim stream
  frames before completion proof, then lose the returned value on observer
  timeout. Gate early claim on actual live delivery; non-rendering calls retain
  stream data until terminal proof or a matching persistent batch boundary.
  Pipelines likewise retain non-rendered output until task-terminal proof.
  One canonical collector remains the owner; no second wait path is authorized.
- R2 is a clarification-only spec follow-up: MF-5's emitted-output sentence
  could state more explicitly that emitted presence does not replace qualifying
  completion proof. The existing approved plan table and stream exclusion
  govern this implementation. No normative change is taken in this slice.
  R3 keeps the unexplained PG worker exit open through final gates. R4 accepts
  reuse of the pure policy's already-derived `batch` predicate. Slice 4 is not
  independently accepted yet and the real fanout remains gated.

- R1 firing RED retains the producer's real publication ordering: all four
  non-rendering one-shot stream, persistent stream, pipeline stream and pipeline
  ordinary-value cases fail exact pending-body/ID retention before repair in
  `/tmp/weft-slice4-nonrendering-custody-red.txt`. The shared drain owner now
  gates early claims on `emit_stream or include_stream_events`; non-rendering
  stream/batch/pipeline collection waits for its qualifying proof. Existing
  ordinary-value fallback and live delivery remain intact. R4 reuses `batch`.
- R1 candidate verification passes 94 focused consuming/activity/settlement
  cases, affected SQLite with thirteen intentional PG-only skips, canonical
  mypy450, full Ruff/suppression/diff and 51 document/seam policy cases. Logs:
  `/tmp/weft-slice4-custody-repair-{focused,sqlite,mypy,ruff,policy}.txt`.
  Separate PostgreSQL fails its 600-frame worker again after 410 other passes,
  `/tmp/weft-slice4-custody-repair-pg.txt`. The unchanged targets/runner with
  private timeout logging pass411 with zero skips in
  `/tmp/weft-slice4-custody-pg-diagnostic.txt`; no timer fires, and the 600-frame
  case finishes in 23.566 seconds under its unchanged 30-second marker.
  The repeated non-instrumented exit remains unexplained, not diagnosed or
  fixed by a passing diagnostic. Keep R3 open as a final-readiness limitation.

- Fresh full-reading Claude review returns FAIL with S1 in
  `/tmp/weft-result-slice4-custody-review.txt`: presentation flags alone cannot
  authorize early claiming ordinary pipeline values, which are buffered rather
  than actually delivered. Root independently found the same counterexample.
  Repair per-row eligibility in the existing shared drain, retaining ordinary
  values while delivering eligible stream frames, including mixed surfaces.
  A bounded public pending-row snapshot ends before yielding; exact claims
  retain competing-reader semantics and avoid repeated oldest-row peeks.
- S2 is a separately flagged one-shot multi-row ambiguity: agent
  `output_mode="messages"` can publish multiple ordinary values, while the
  historical one-shot fallback treats a single observed value as unambiguous.
  The approved fallback boundary needs an owner/spec decision before changing
  that contract. It is not counted as a slice-4 blocker by the reviewer and is
  not silently declared resolved. S3 accepts avoiding unnecessary framing
  inspection after qualifying proof or in pipeline mode, plus simplifying its
  bounded generator expression. Persistent ordinary backlog framing scans on
  unrelated real activity remain a task-local cost to evaluate separately.

- S1 expanded real custody matrix: original four cases pass; all four added
  live cases fail exact pending-row retention on the pre-repair source in
  `/tmp/weft-slice4-live-value-custody-red.txt`. Includes public text emission,
  canonical stream events, shipped cmd_result(stream=True), and a real mixed
  pipeline surface. Current candidate passes98 focused cases, affected SQLite
  (13 intentional PG-only skips), canonical mypy450, full Ruff/suppression/diff
  and 51 policy cases. Artifacts:
  `/tmp/weft-slice4-live-value-custody-{focused,sqlite,mypy,ruff,policy}.txt`.
  Standard non-instrumented isolated PostgreSQL passes415 with zero skips in
  `/tmp/weft-slice4-live-value-custody-pg.txt`. The bounded snapshot plus exact
  eligible claims removes repeated oldest-row peeks without holding operations
  across yields. This passing gate does not diagnose prior worker exits.

- Slice 4 accepted closure: fresh full-reading Claude returns completed PASS
  and NO_FINDINGS in `/tmp/weft-result-slice4-live-value-review.txt`, explicitly
  confirming all thirteen required files. R1 and S1 are closed; it independently
  verifies bounded operations before yields, exact competing claims, per-row
  eligibility, persistent batch matching, and removal of unnecessary framing
  scans. Slice 5 is released. Recorded S2/S3/R2/R3 and inherited presentation
  observations remain explicit follow-ups; no shipping/readiness claim follows
  from this acceptance.

- Slice 5 real operational probe passes on SQLite and isolated PostgreSQL after
  independent slice-4 acceptance. Forty accepted Task.result callers wait on
  custom routes while child launch is gated before initialization; 1625 foreign
  3500-byte rows plus three causal unrelated/malformed/non-object wakes produce
  zero application transfer/decode of those rows while queued. Actual execution
  returns39 structured briefings and one real failure. Both backends fetch
  448092 bytes of lifecycle candidates and perform320 application decodes across
  delivery. Accepted configuration binds exactly40 times (SQLite0.003154s,
  PostgreSQL0.002768s total); these are binding times, not wrapper setup times.
- SQLite fallback watchers fetch19936834 history-body bytes separately;
  PostgreSQL uses native activity and fetches0 fallback-watcher bytes. The160
  observer queue leases remain stable through unrelated wakes and all close.
  At the waiting checkpoint SQLite sees202 post-hook physical objects (201live,
  including160 watcher connections); PostgreSQL sees45 created (3live), plus a
  pre-hook pooled object accounted separately. All idle, unloaned PG pool
  objects are IDLE, no observer checkout remains while waiting, and no physical
  connection is added across the three unrelated wakes. After results SQLite
  retains only the writer connection; PostgreSQL's shared pool remains owned
  by manager/writer leases. All observed drivers and pool objects close after
  complete harness disposal.
- Initial PG probe failed only its post-disposal bookkeeping assertion: actual
  drivers were all closed, but instrumentation was restored before the harness
  returned its last manager checkout. Fix extends hook lifetime through harness
  disposal; the same custody and closure assertions are unchanged. Both backend
  probes pass after this fixture correction. No production change is made to
  accommodate it. Evidence: `/tmp/weft-real-fanout-custody-{sqlite,pg}.txt`,
  `/tmp/weft-real-fanout-metrics.jsonl`, final executed source
  `/tmp/weft-real-fanout-executed-v2.py`, SHA256
  `ad3f1b6799c83e18d88200d383952234758364ade8ecaab8717ad8b4092949ca`.
  Reproduce by copying that private artifact to the temporary explicit target
  `tests/commands/_real_fanout_operational_probe.py`, running
  `.venv/bin/python -m pytest -m "" tests/commands/_real_fanout_operational_probe.py -q`
  and `./bin/pytest-pg tests/commands/_real_fanout_operational_probe.py`, then
  removing only that owned temporary file. It is removed before final gates.
- Probe scope:40 caller threads in one parent process, sequential real children,
  peak1 consumer wrapper. It proves result discovery/observation, not40 separate
  parent process pools, simultaneous child startup, production CPU utilization,
  or admission capacity. Physical counts exclude connections opened before the
  hook except explicitly observed pooled objects; child processes are separate.


### Slice 5 final verification and boundary closure (2026-10-09)

- First full-fast gate: 8 failed, 5445 passed, 33 skipped. Six failures exposed
  prohibited imports or missing explicit shared-module registrations. Repairs
  preserve architecture guards: CLI uses the existing command codec reexport;
  the Task handle obtains its TaskSpec type through submission; result policy
  obtains the Monitor record type from the shared history owner. The existing
  callback function moves unchanged to `weft/helpers/__init__.py` for Weft and
  Django reuse, rather than adding an ext-to-core import or a second helper.
  Tests obtain the actual broker class from a public context loan rather than
  importing private `simplebroker.db`.
- The two startup failures were impossible fixture launches returning None.
  The fixture now supplies the actual DetachedManagerLaunch value shape and
  checks ManagerStartFailed retains the exact observer cause, cleanup of its
  owned launcher, and queue closure. No production None guard is added.
- Full-fast rerun: `.venv/bin/python -m pytest`, 5455 passed, 33 intentional
  backend skips, 163.59 seconds; `/tmp/weft-implementation-final-fast-v2.txt`.
  Full slow-inclusive gate: `.venv/bin/python -m pytest -m ""`, 5456 passed,
  44 environment/backend skips, 257.51 seconds;
  `/tmp/weft-implementation-final-all.txt`.
- Canonical 450-source mypy and full Ruff pass; suppression index --check and
  git diff --check pass. Evidence: `/tmp/weft-implementation-final-mypy-v2.txt`,
  `...-ruff-v2.txt`, `...-index-v2.txt`, and `...-diff-v2.txt`.
- Installed `.venv/bin/weft` operational acceptance runs from this repository
  with isolated harness contexts on SQLite and isolated PostgreSQL. README
  run/status/result smoke passes; malformed accepted spawn is followed by a
  healthy result under the same manager TID and runtime handle. Public pending
  spawn queue is empty afterward. Listener fault injected at SimpleBroker's
  public waiter factory reaches the real CLI as exit 1 with a concise observer
  diagnostic and no traceback. The command/client keeper separately proves
  retained causes and resource cleanup. Both probes pass, 2 cases per backend
  (SQLite 4.31 seconds, PG 7.00 seconds). This does not touch the repository's
  ambient/production broker. It is not evidence about a default production
  context or target stderr that intentionally contains a traceback.
- Initial private acceptance-probe assertions assumed empty queue peek exits 0
  and underlying listener causes are rendered. Actual documented surfaces use
  exit 2 for an empty peek and the typed observer diagnostic while retaining
  causes internally. Correcting these probe expectations changes no production
  code. Executed artifact: `/tmp/weft-result-acceptance-operational-probe.py`,
  SHA256 `d8e1a4b48ba4e065402031fb93e9ee4b51a255d2e31e779ea2a8a04208bbdb50`.
  Reproduce using the temporary explicit target
  `tests/commands/_result_acceptance_operational_probe.py`, pytest -m "" and
  bin/pytest-pg, then remove only that owned file. It is removed before final
  gates. Logs: `/tmp/weft-result-acceptance-operational-sqlite-v2.txt` and
  `/tmp/weft-result-acceptance-operational-pg.txt`; sanitized command outcomes
  are in `/tmp/weft-result-acceptance-{smoke,observer}-{sqlite,postgres}.json`.
- Shipped missing/invalid/omitted TID and zero-timeout cases now explicitly
  exclude CLI tracebacks. Existing failed Python-target results intentionally
  contain the worker's captured traceback as the published error; the keeper
  pins that exact diagnostic and excludes CLI/command frames instead of
  silently truncating it to satisfy an overly broad tool-error assertion.
  No universal no-traceback claim is made for target diagnostics. Any broader
  one-line target-error policy requires a separate spec/owner decision.
- Strict traceability found a stale PY-2 owner mapping after the shared helper
  move. It is corrected along with the new CLI invocation keeper mapping.
  Final strict comparison and final PostgreSQL/reviewer disposition follow.

- Broad final PG gate: 1 worker exit, 951 passed, 1 explicitly SQLite-only
  physical-count skip in 81.87 seconds. The crash item is the same 600-frame
  prefix keeper; `/tmp/weft-implementation-final-pg.txt`. Unchanged isolated
  rerun with the private original-timer logging hook passes (11.24-second call,
  12.13-second run), with no recorded fired timer. This does not diagnose R3.
- Read-only source review identifies a separate fidelity bug in that keeper:
  its caller-owned outbox defaults to persistent=False, reconstructing a
  DBConnection facade for each of 600 writes. Production BaseTask registers
  persistent queues and Consumer obtains its cached outbox. One-keyword
  persistent=True correction mirrors retained producer behavior while preserving
  caller-owned queue cleanup. All600 frames, exact chunk/value/empty assertions,
  timeout marker30 and result timeout0 are unchanged. No production code or
  timeout/oracle is altered. R3 remains causally unconfirmed; passing corrected
  gates will not be presented as proof that the earlier worker exit was fixed.


### Final implementation handoff (2026-10-09)

All five implementation slices are present in this uncommitted worktree. No
commit, push, release/version/dependency-floor change, CI change, or sibling
SimpleBroker edit is made. The implementation is reviewable, not land-ready.

- Final current-source slow-inclusive suite: 5456 passed, 44 environment/backend
  skips, 282.72 seconds; `/tmp/weft-implementation-final-all-v2.txt`. This rerun
  includes the fast suite and the retained-writer600 keeper. Canonical mypy450,
  full Ruff, suppression-index --check, and diff whitespace checks pass again;
  artifacts `/tmp/weft-implementation-final-{mypy,ruff,diff}-v3.txt` and
  `/tmp/weft-implementation-final-index-check-v3.txt`.
- Final isolated PG suite: 952 passed, 1 explicitly SQLite-only physical-core
  count skip, 81.75 seconds; `/tmp/weft-implementation-final-pg-v3.txt`.
  The corrected600-frame keeper takes4.03 seconds under the broader run,
  preserving its original30-second marker,600frames, full sequence/final value
  and pending-empty assertions. Relevant PG-only physical connection and native
  activity tests fire. Exact37 targets are captured in
  `/tmp/weft-implementation-final-pg-targets.json`; invoke bin/pytest-pg with
  those targets after loading .envrc. The v2 restart omitted .envrc and was
  explicitly interrupted (exit130), not counted as a gate. The v3 run loads the
  required repo environment and completes. No backend-heavy jobs run together.
- Fresh final Claude review completes with exit0 and explicit full13 required
  reading (including writing-plans and adversarial-acceptance-probes):
  REVIEW_VERDICT PASS for implementation quality/scope, READINESS blocked,
  NO_FINDINGS for new scoped blockers. Artifacts:
  `/tmp/weft-result-final-review.json`, `/tmp/weft-result-final-review.txt`, and
  `/tmp/weft-result-final-review-prompt.txt`. Reviewer read a still-running
  slow-inclusive log and had no completed final PG log; the completed gates
  above supersede only those verification observations. No implementation
  source changes follow the review. They do not waive its other blockers.
- Final architecture/spec/plan/suppression/audit-policy gate passes109 cases;
  `/tmp/weft-implementation-final-policy.txt`. Strict Backstitch remains
  NONPASS31errors1170warnings, compared with baseline31/1171; normalized issue
  keys introduce ZERO issues. Artifacts:
  `/tmp/weft-backstitch-implementation-final-v3.json` and
  `/tmp/weft-backstitch-final-comparison-v3.json`. No debt waiver or weaker gate.

Separate actions remain explicit, with their boundaries:

- Weft owner: choose the paired Weft/weft-django release versions, dependency
  floors, upgrade guidance and integration release-note destination before
  shipping uncapped cancellation. The cross-package helper is currently the
  private `weft.helpers._register_observation_wakeup`; record it in that paired
  handoff or make an explicitly reviewed public contract. No new public surface
  is added by this slice.
- Weft spec owner: decide the published target-error rendering contract.
  Captured worker tracebacks are currently rendered verbatim, while actual CLI
  invocation/observer failures have concise, traceback-free diagnostics.
  There is no explicit governing spec exception for target tracebacks. The
  keeper probes exact published diagnostics; this work does not silently
  redefine the generic adversarial floor or promote a new target-error policy.
- Weft owner: S2 includes one-shot messages ambiguity and inherited eager claim
  of an ordinary value during quiet grace. Observer failure or abandoned follow
  in that window can still lose that value. This is not claimed fixed. S3 also
  covers repeated task-local outbox body decoding through final-evidence,
  realtime value, framing and activity reads. Neither is shared-log replay;
  each needs a separate minimal spec/implementation decision. R2 wording and
  inherited ctrl_out rendering remain separately noted.
- Weft/test owner: R3 earlier abrupt PG worker exits remain causally unconfirmed.
  The source-proven retained-writer fixture correction and passing broad rerun
  are not presented as a diagnosis of those exits.
- SimpleBroker owner, separate repository: fresh facade plugin/schema setup,
  semantic/indexed TID lookup and SQLite watcher body transfers. This change
  uses public broker interfaces and avoids repeating facade setup on retained
  named operation owners; it does not fix upstream costs. Admission reservation
  for launched-not-connected processes remains a separate Weft slice.
- Landing owner: initial dirty submission-facade work is present in this tree.
  Preserve its attribution and stage separately; this task does not claim its
  authorship or authorize a commit. The original docs/lessons.md byte prefix
  remains intact, checked against its initial SHA256; only the dated custody
  lesson is appended. Other initial dirty changes are retained.

Current worktree inventory below includes prior dirty files and this task's
edits. `D` entries are explicitly approved retired code/tests, not live modules;
`??` entries are untracked. This inventory is not a staging instruction.

```text
 M docs/lessons.md
 M docs/plans/README.md
 M docs/ruff-suppression-registry.md
 M docs/specifications/03-Manager_Architecture.md
 M docs/specifications/04-SimpleBroker_Integration.md
 M docs/specifications/05-Message_Flow_and_State.md
 M docs/specifications/10-CLI_Interface.md
 M docs/specifications/13C-Using_Weft_With_Django.md
 M docs/specifications/14-Python_API_Surfaces.md
 M integrations/weft_django/tests/test_weft_django.py
 M integrations/weft_django/weft_django/channels.py
 M integrations/weft_django/weft_django/realtime.py
 M integrations/weft_django/weft_django/sse.py
 M tests/cli/test_cli_result.py
 M tests/cli/test_result_claimed_json.py
 M tests/commands/test_control_observation_connections.py
 M tests/commands/test_event_observation_connections.py
 D tests/commands/test_interactive_client.py
 D tests/commands/test_interactive_exit_terminal.py
 M tests/commands/test_manager_commands.py
 M tests/commands/test_observation_connections.py
 M tests/commands/test_queue.py
 M tests/commands/test_realtime_events.py
 M tests/commands/test_result.py
 M tests/commands/test_result_observation_connections.py
 M tests/commands/test_run.py
 M tests/commands/test_run_public.py
 M tests/commands/test_submission.py
 M tests/commands/test_task_commands.py
 M tests/conftest.py
 M tests/core/test_client.py
 M tests/core/test_manager_runtime_connections.py
 M tests/core/test_ops_shared.py
 M tests/core/test_queue_wait.py
 M tests/core/test_spawn_requests.py
 M tests/specs/test_command_queue_seam.py
 M tests/specs/test_ruff_policy.py
 M weft/_constants.py
 M weft/cli/app.py
 M weft/cli/run.py
 M weft/client/_client.py
 M weft/client/_task.py
 M weft/commands/_result_wait.py
 M weft/commands/_streaming.py
 M weft/commands/_task_history.py
 M weft/commands/events.py
 D weft/commands/interactive.py
 M weft/commands/queue.py
 M weft/commands/result.py
 M weft/commands/run.py
 M weft/commands/submission.py
 M weft/commands/system.py
 M weft/commands/tasks.py
 M weft/core/manager_runtime.py
 M weft/core/outbox.py
 M weft/core/queue_wait.py
 M weft/core/spawn_requests.py
 M weft/core/task_evidence.py
 M weft/helpers/__init__.py
?? docs/plans/2026-10-09-burst-result-observation-plan.md
?? docs/plans/2026-10-09-direct-result-observation-implementation-plan.md
?? docs/plans/2026-10-09-direct-result-observation-spec-plan.md
?? docs/plans/2026-10-09-task-specific-result-discovery-placeholder.md
?? tests/commands/test_active_interactive_lifecycle.py
?? tests/commands/test_burst_result_wait.py
?? tests/commands/test_consuming_result_collection.py
?? tests/commands/test_observer_command_failures.py
?? tests/commands/test_point_snapshot_loans.py
?? tests/commands/test_result_activity_waits.py
?? tests/commands/test_result_settlement_policy.py
?? tests/commands/test_retained_result_history.py
?? tests/commands/test_submission_custody.py
?? tests/commands/test_task_history_adapters.py
?? tests/commands/test_terminal_snapshot_loans.py
?? tests/core/test_client_submission_facade.py
?? tests/core/test_manager_start_observer_failure.py
?? tests/core/test_task_history_reader.py
```
