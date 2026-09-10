# Shared Service Convergence Refinement Plan

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.1], [MA-1.4], [MA-1.7], [MA-3]; docs/specifications/07-System_Invariants.md [MANAGER.3], [MANAGER.8], [MANAGER.8a], [MANAGER.9]-[MANAGER.16], [OBS.13.6], [OBS.13.7], [OBS.15], [QUEUE.6]; docs/specifications/05-Message_Flow_and_State.md [MF-3.1], [MF-3.2], [MF-5], [MF-6], [MF-7], Failure Recovery Flow; docs/specifications/01-Core_Components.md [CC-2.2], [CC-2.3]; docs/specifications/10-CLI_Interface.md [CLI-1.1.2]; docs/specifications/02-TaskSpec.md [TS-1.1], [TS-1.2]; docs/specifications/00-Quick_Reference.md queue catalogue; docs/specifications/04-SimpleBroker_Integration.md [SB-0.1], [SB-0.4a]
Superseded by: none

Class: 5. Implementation with spec revision. Hardening applies to execution,
service ownership, retention, and shutdown. Promotion strategy B: specs, code,
tests, and reciprocal mappings land together. This is a proposal, not a claim
that these changes are implemented or an instruction to deploy them.

## Goal

Use one convergence algorithm for every authorized service, including the
Manager. Within the existing broker scope and service key, responsive owners
converge on the lowest TID for future shared work. They preserve already-owned
work according to its explicit disposition policy. The same decision drives
supervision, service participation, and service-backed endpoint selection.

The guarantee is eventual convergence and pickup of every eligible available
work item under the conditions below. This plan does not substitute a success
percentage for a proof. It distinguishes operating assumptions, explicit policy
exceptions, and bugs. An indefinitely unavailable broker or a system with no
remaining launcher cannot provide unconditional progress.

Convergence means one preferred owner of future work per service key, not
necessarily one live process. Older managers can still own children, and older
services can still have work to finish. This ownership split is already Weft's
design. The refinement makes it consistent across the implementation.

## Refinement, Improvement, and Simplification

The current core is sound: everything is a Task; queues hold durable facts;
service identity groups equivalent owners; lowest TID gives deterministic
incumbent preference; reservation owns individual work; and supersession does
not transfer children. Preserve those choices. Do not describe them as new.

Current code has several implementations of what should be the same decision:
manager leadership and supersession, managed-service candidate reduction,
heartbeat endpoint-based ownership, and endpoint runtime liveness. Their
readiness, uncertainty, and retirement rules differ. Sharing only `min(tid)`
would leave those differences intact.

| Current rule/path | Proposed refinement | Why it is better; what becomes simpler |
| --- | --- | --- |
| Manager voluntary yield blocks on persistent children; automatic notice takes an irreversible drain path. | Defer new shared intake through the ordinary reactor; owned work continues. | Convergence no longer depends on child lifetime. Remove leader-specific drain/revalidation/resume state. |
| Manager, managed-service, and heartbeat endpoint paths disagree about live/ready owners. | One evidence fold, keyed readiness predicate, proof-renewal rule, and numeric-TID selection per service key. | A Docker owner gets the same answer from supervisor, participant, and endpoint lookup. Remove competing liveness-to-ownership shortcuts. |
| Managed-service duplicate convergence sends KILL and sometimes force-kills a process. | Automatic convergence withdraws future shared work; disposition of owned work follows the table below. | Automatic preference no longer doubles as destructive process authority. Explicit STOP/KILL remains explicit. |
| An `ensure` service can remain in `degraded_wait` forever for an unresponsive owner. | After bounded readiness uncertainty, permit a lifecycle-policy-allowed replacement; the old owner remains unresolved, not dead. | A stale claim cannot permanently prevent service recovery. The same availability rule applies to managers and other services. |
| Owner heartbeats and foreign supersession notices compete as versions of one status. | Fold owner observation and binding explicit intent separately, in the shared service fold. Automatic notices are hints to reevaluate. | Remove heartbeat delete/recreate repair and special irreversible automatic-notice handling. No notice graph or acknowledgement protocol. |
| Internal spawn priority is absolute; single-flight launching can starve public work. | Prefer internal work initially, then alternate eligible internal/public launches while both remain backlogged. | Each lane has a bounded opportunity to progress, without a configurable scheduling framework. |
| Stale internal-reservation cleanup deletes every message for an unprotected owner. | Never erase an unresolved accepted launch merely because its manager disappeared. Preserve exact work and existing recovery policy. | Pipeline children cannot disappear silently during cleanup. This fixes loss; it is not a policy exception. |

These are explicit spec changes. The gain is fewer independent mechanisms and
stronger progress, not a claim that all behavior was already equivalent.
The cost is temporary overlapping owners during uncertainty, asynchronous probe
state, and potentially long-lived supervising processes. These costs follow
from availability and preservation of owned work; they must not be concealed.

## Source Documents and Spec Baseline

Baseline: commit `178e3a34`. Recheck symbols against the implementation start
revision. Current specs remain authoritative until atomic promotion.

Read the agent context in its prescribed order, then the source sections in the
metadata block and the writing-plans, hardening-plans,
review-loops-and-agent-bootstrap, runtime-and-context-patterns, and
testing-patterns runbooks. Read both lessons files.

Recorded reasons for current complexity:

- `2026-04-24-manager-status-container-pid-liveness-plan.md`: container PID 1
  was mistaken for host identity. Namespace-local PID observations do not prove
  that the recorded task is ready.
- `2026-05-11-manager-work-stealing-dispatch-plan.md`: stale ownership evidence
  stranded public dispatch under PostgreSQL load. Keep atomic reservation as
  authority; do not restore a global manager fence around all service work.
- `2026-05-09-internal-spawn-priority-queue-plan.md`: internal services were
  stuck behind ordinary FIFO backlog. Keep their separate lane and prompt first
  opportunity, but remove unlimited starvation of the other lane.
- `2026-05-08-deterministic-manager-service-reconciler-plan.md`: uncertainty
  deliberately became visible degraded waiting rather than permission to start
  duplicates. This plan changes that tradeoff to eventual availability with
  temporary overlap and non-destructive convergence.
- `2026-05-13-manager-liveness-and-leadership-robustness-plan.md`: a yielding
  manager must recover when its predecessor disappears. Keep the requirement;
  ordinary reevaluation replaces the special drain/resume mechanism.
- `docs/lessons.md`, replacement-heartbeat and May 20 manager drain entries:
  heartbeats must not erase explicit intent; automatic convergence must not
  kill user children.

Comprehension check: why can a deferring manager finish a reserved launch, but
not destroy a timed-out owner's reservation? Reservation owns the first
operation; missing readiness does not prove death or authorize the second.
Why must a capable deferring owner remain eligible in PONG? Otherwise mutual
withdrawal could be mistaken for universal unavailability.

## Scope and Participants

Preserve `manager_service_key()`, `service_context_key()`, broker targets, and
existing logical service keys. Different backends remain separate. Different
PID namespaces can use the same backend configuration. Path/DSN alias handling
and normalization of differently named autostart manifests are not this task.
The same algorithm applies per key; it cannot infer that different keys were
intended to name the same service.

Participants are Managers, Heartbeat, TaskMonitor, LivenessMonitor, and
manager-authorized autostart services, including Consumer and pipeline runtime
classes. Ordinary user Tasks do not become singletons because their name or
class matches. Generic endpoint claims are addresses, not service membership or
permission to retire their owners.

Do not put the policy exclusively in `ServiceTask`: it supplies worker mechanics
and autostart services can inherit directly from `BaseTask`. Reuse the existing
`service_convergence.py` as the common policy owner, reached by authorized
service-keyed tasks through bounded BaseTask reactor hooks. Manager resolves membership through
its existing trusted builtin/autostart path (`_trusted_service_key_from_metadata`
and the internal runtime envelope), then passes the approved identity in the
internal launch context. BaseTask does not infer membership merely from
`taskspec.metadata` or the permissive `service_key_from_metadata` helper.
Manager membership is structural. Public metadata cannot enroll another task.
The common pre-turn hook advances facts/probes/preference only; it must never
skip a concrete reactor's owned-work processing. Merge its deadlines at the
shared owner-loop wait boundary reached by all overridden `next_wait_timeout`
methods, rather than relying on subclasses to remember a `super()` call.

## Context and Key Files

| Owner | Code and current coupling |
| --- | --- |
| Common facts/decision | `weft/core/service_convergence.py`: owner parsing/projection, latest-row reduction, TTL, canonical selection. |
| Lifecycle policy | `weft/core/manager_services.py`: `summarize_service_candidates`, `reduce_managed_service_state`, `once`/`ensure`, pending launch, backoff, restart limits. Keep desired-lifecycle policy; replace its separate ownership decision. |
| Task participation | `weft/core/tasks/base.py`, `service.py`, `consumer.py`, `pipeline.py`: common control/reactor entry and task-specific acquisition/completion boundaries. |
| Manager | `weft/core/manager.py`: registry publication, both PONG paths, leadership/drains, `_process_queue_message`, `_queue_counts_as_wait_activity`, `next_wait_timeout`, service reconciliation, duplicate killing, reservation cleanup. |
| Shared control | `weft/core/control_probe.py`: keyed matching and readiness validation. Current generic service probes accept a matched PONG without the manager's eligibility checks. |
| Discovery/adapters | `weft/core/manager_runtime.py`, `weft/core/endpoints.py`, `weft/core/heartbeat.py`; manager/system commands and client namespaces. Endpoint and supervisor selection must agree. |
| Concrete services | `weft/core/tasks/heartbeat.py`, `weft/core/monitor/task_monitor.py`, `weft/core/tasks/liveness_monitor.py`: new-cycle and owned-work boundaries. |
| Retention | `weft/core/pruning/runtime.py`, TaskMonitor service-owner/destruction protection, Manager `_cleanup_stale_internal_reserved_queues`. Exclusion is not death. |
| Verification | Existing manager, managed-service, control, endpoint, heartbeat, monitor, pipeline, pruning tests; `tests/helpers/reactor_driver.py`, `weft_harness.py`, `bin/pytest-pg`. |

## Proposed Spec Delta: One Shared Algorithm

Promote the detailed service-convergence contract into [MA-1.7], with [MA-1.4]
referring to it for Manager participation. Keep one detailed algorithm; other
specs summarize their boundary and link to it.

### 1. Common owner facts and durable intent

For each existing `(service_key, owner_tid)`, fold the latest owner observation,
trustworthy terminal evidence, and binding explicit supersession separately.
Terminal evidence wins for that TID. A later active heartbeat cannot erase
binding exclusion. This common fold serves managers, managed-service supervision,
participant self-checks, and service-backed endpoints.

Keep v2 owner rows and existing queues. New explicit writers use existing
metadata with `supersession_reason="explicit_supersession"`, replacing any
inherited automatic reason. Existing well-formed automatic notices use
`higher_tid_active_refresh_seen` and a valid strictly lower `superseded_by` TID.
Generalize that automatic interpretation to same-key service owners. Untagged
legacy superseded rows retain binding meaning. Malformed hints prove no readiness.

An automatic notice means reevaluate the shared rule. It is not terminal
lifecycle evidence, STOP, or a permanent dependency on its author. Periodic
reevaluation makes delivery of every hint unnecessary for convergence.
An explicit notice permanently excludes its target TID from future shared work
once observed; it does not itself transfer or cancel owned work. Existing
operator replacement still sends STOP and a notice. No new control command.

Authorized participants refresh their own existing owner row while alive,
including during reversible deferral. Today only managers refresh reliably;
managed-service launch hints alone are insufficient for long-lived peer discovery.
The manager still publishes launch intent/evidence until the child can publish
its own presence. A service restart gets a new TID. Preserve the existing
service key, role, routes, authority metadata, and runtime handle when refreshing.

Self-compaction preserves the latest observation and at most one equivalent
binding notice. Automatic hints can expire. Age, unknown identity, and a newer
heartbeat do not authorize removing binding intent. Remove it only with
trustworthy same-TID terminal/dead evidence and the older rows that could
otherwise resurrect that owner. An unreachable owner may leave one small notice
indefinitely. This is the explicit retention cost of observing intent after an
arbitrarily long pause; do not introduce a new durable acknowledgement protocol.

Preserve existing cleanup custody. Any extension of participant self-compaction
applies only to rows for its own authorized service/TID; it does not grant a
manager permission to delete peer service rows. Runtime pruning handles peers.

### 2. One readiness predicate and renewal rule

Readiness requires a correctly matched existing keyed PING/PONG for the exact
owner TID. Validate active task status, not paused, not stopping, and the
recorded task control routes. Verify service membership from authorized registry
and launch evidence; manager participation additionally validates its canonical
public request/output routes. Add no new PONG message type. Self knows its own
local eligibility without pinging itself. Deferral alone does not make a capable
participant ineligible; explicit exclusion and actual stopping do.

Local PID existence, absence, or creation-time collision neither grants readiness
nor suppresses PING. Runtime observations remain useful for properly scoped death
and process-control questions. Local task-root path equality is not service
readiness authority. The existing service scope, exact TID, and bound control
queues identify the participant.

Use one asynchronous probe/positive-cache implementation for all participants
and service selection. Key cached proof by stable owner identity and validated
routes, not heartbeat message ID. Use monotonic time. Heartbeat refresh does not
cancel a probe or reset its deadline. Reader adapters drive the same exchange within their existing bounded call;
reactors never block waiting for a reply. Cache is local to the owning reactor
or reader invocation/session, not a new process-global authority. Within its existing call budget, a cold reader can retry completed timeouts
with the learned budget; it does not abandon discovery solely because the first
50 ms attempt failed. When the overall call budget expires without proof, it
returns the existing not-ready/not-found outcome; it does not hang or silently substitute PID proof.
Longer-lived callers can retry and retain their local learned budget; one-shot
CLI callers retain their existing finite timeout/error surface.

Renew positive proof at the existing refresh cadence. Retain it while renewal
is due for scheduling or pending; a pending first probe has no positive authority.
Clear proof on completed timeout, transport failure, matched ineligibility,
exclusion, or identity change. Otherwise cache renewal would reopen intake on
every refresh forever. Advance all pending probes independently of the budget
for starting new ones. Start due probes oldest-due-first, TID as tie-breaker,
without resetting their due time while queued. A finite candidate set then gives
each due probe a bounded number of start opportunities.

Use the existing manager probe timeout as the initial common budget, doubling
that owner's budget on completed timeout and retaining the learned budget across
successes. No new configuration knob or fixed upper cap. Each exchange has a
finite deadline starting at successful send. Poll replies on ordinary turns.
This permits eventual convergence when the eventual finite latency bound is
unknown, rather than assuming every Docker round trip fits today's 50 ms.
A slow owner can consequently take longer to fail over after a later crash;
there is no fixed worst-case failover guarantee. Keep retry scheduling bounded
and fair; transport errors cannot preserve positive proof indefinitely.

### 3. Selection, withdrawal, and replacement

The shared decision selects the lowest numeric TID among ready, non-excluded
owners of one service key. Every caller uses that result. A participant with a
lower ready peer defers future shared-work acquisition; without one it may
acquire work under its normal policy. Unknown is not a global fence. Automatic
notices merely prompt this calculation. No remembered-predecessor graph,
manager-only election, endpoint-only election, or duplicate-service KILL path.

Supervision asks the same decision whether a usable owner exists. A pending
probe/launch gets a bounded chance to finish. If no owner can be proved ready
within the discovery budget, a desired service may launch a replacement under
its existing `once`/`ensure`, backoff, and restart-budget rules. Replace permanent
`wait_uncertain`/`degraded_wait` vetoes with retryable unavailability. Do not mark
the former owner dead, replay its uncertain reserved work, or erase its evidence.
A returned or delayed owner joins the same minimum-TID comparison.

A known pending source request must be consumed rather than generating duplicate
launch requests. An unresolved reserved launch is an owned-work recovery case,
not a blank cheque to spawn more copies. Bounded launch recovery and the explicit
exceptions below govern it. Replacement attempts must respect existing backoff
and pending-intent suppression; one completed uncertainty interval cannot create
an unbounded spawn burst in a single reactor turn. Carry the adaptive discovery
budget across replacement TIDs in the existing per-service uncertainty state;
do not reset the replacement sequence to 50 ms at each new launch. Give the
new candidate that bounded discovery window before another replacement attempt.
A ready owner ends the replacement sequence. With stable finite startup/control
delay and progressing launch operations, this prevents the algorithm itself from manufacturing endless arrivals
by abandoning every new owner before it can answer.

Manager bootstrap follows the same readiness rule. It does not return a fresh
but unresponsive row as a proven reusable manager; after bounded discovery it may
launch a helper. Preserve local new-process identity/readiness proof and startup
error reporting. No separate namespace/backlog/grace eligibility algorithm.

### 4. Task-specific boundaries, not task-specific elections

| Participant | Future shared work to defer | Already-owned work and retirement |
| --- | --- | --- |
| Manager | New reservations from the public spawn source. Internal service supervision remains per service key, never fenced by global manager preference. | Finish both owned reserved lanes, active launch results, user children, and local service/autostart obligations through the ordinary reactor. Persistent children do not block intake convergence. |
| TaskMonitor | Starting another global monitor/cleanup cycle. | Finish and commit in-flight processor, builtin-cycle, and cleanup results through existing owner-thread paths before retirement. Do not kill a worker midway through a queue/store effect. |
| LivenessMonitor | Starting another global reconciliation/probe cycle. | Finish existing probe/result application and exact owned cleanup work. Preserve its exclusive tid-mapping custody and current restart semantics. |
| Consumer/autostart/pipeline service | New reservations from a configured input queue shared by owners of that service; new logical service endpoint selections target the preferred owner. | Preserve current invocation, reservations, pipeline children, and inputs already addressed to this TID. A running whole-program service may have no safe intermediate withdrawal point and may remain alive. |
| Heartbeat | New reservations from its stable service inbox; new due emissions after local confirmed withdrawal. | Finish the bounded turn and owned mutation. Leave unreserved mutations on the shared inbox for the preferred owner. Stop old emissions and discard volatile registrations under E4; no timer-state transfer. |

For the acquisition hook, the canonical `T{tid}.inbox` and `T{tid}.reserved`
are addressed/owned queues, not shared intake. A configured input queue common
to service instances is shared intake; Manager's public source and Heartbeat's
service inbox are the explicit builtin cases. Do not gate every BaseTask queue
merely because membership exists. Controls and result paths remain active. In particular, Heartbeat's
`_drain_one_registration_message` moves rows directly and must consult the same
preference before its move; gating BaseTask's generic queue method alone misses
that path. TaskMonitor gates new `_run_monitor_cycle`/builtin-cycle submissions,
not `_handle_builtin_cycle_worker_result`. LivenessMonitor gates both new
reconciliation in `_reconcile_mapping_rows` and new scheduling in
`_schedule_due_probes`, not `_apply_probe_result`. Consumer's active result
commit and PipelineTask's already-started child graph remain owned work.

TID-local input is addressed work, not a shared source another owner can simply
claim. Do not abandon queued TID-local requests on automatic retirement or
pretend endpoint reselection moves them. An automatically withdrawing owner
must retain/process its addressed input and owned work; if it cannot establish
a safe retirement boundary, it stays alive. Explicit STOP, task completion,
and crash retain their existing lifetime/recovery semantics. A cached obsolete
endpoint or a direct write to a dead TID does not create a new live recipient.
Service-backed endpoint lookup must use the common owner decision; ordinary
non-service endpoints retain their existing independent task-address semantics.

Manager deferral does not set `_draining`, unregister active presence, or make a
Task state go backward. Actual STOP/KILL/signals retain the pre-launch fence and
bounded child-stop behavior. STOP after an explicit notice must still begin its
real drain, rather than returning early because convergence already set a drain
flag. Notice alone is not STOP. Publication is not a linearizable cutover: work
reserved before the target observes notice remains governed by reservation and
control policy.

Suppress deferred shared backlog as immediate wait activity, but keep probe,
renewal, control, and owned-work deadlines. Otherwise a follower either spins
or never notices that the preferred owner disappeared. Empty duplicates may
exit while a lower owner remains ready. Check claimed as well as unclaimed own
reservations. Do not use Manager's `_manager_owned_work_pending` or
`_managed_service_convergence_active_reasons` unchanged as an exit predicate;
they include shared backlog or due scans. Builtin supervision children follow
the existing manager cleanup policy rather than preventing exit forever.

### 5. Heartbeat command delivery

Give Heartbeat one stable ordinary work inbox, `weft.heartbeat.requests`, bound
through its existing TaskSpec input wiring. Keep each instance's own reserved,
control, and output queues. Publish that stable inbox in every heartbeat endpoint
claim; helpers write it after ordinary service discovery. Payloads and public
helper signatures are unchanged. An endpoint chosen just before its owner exits
therefore still addresses a queue the replacement can consume. This is a work
queue, not a coordination/lease queue or a timer database.

This one queue is necessary to remove the resolve-old-TID/write-after-exit race.
Forwarding through old private inboxes would require keeping old processes alive
or a forwarding/recovery protocol. Stable addressing uses existing SimpleBroker
reservation and gives this command the same ownership boundary as public spawn.
Register the constant and queue catalogue/dump behavior explicitly; pending
mutations are durable work and runtime pruning must not delete them.

A losing heartbeat finishes any reserved mutation, then stops emitting and
clears its volatile timer heap when its own reactor observes withdrawal. Other
observers may select the new owner first: transient timer overlap is possible;
no ordered, gap-free, duplicate-free timer handoff is claimed. Unreserved
mutations remain for the preferred heartbeat. An already consumed registration
may be lost under E4; do not replay its command as if it were unconsumed.
Upsert/cancel go to the shared inbox and are processed by its preferred owner.

This does not rewrite ordinary named endpoints into durable delivery services.
Their explicitly addressed semantics remain E5 below. Builtin heartbeat commands
are strengthened here because their helper provides logical service mutation,
and its current private addressing creates a concrete pickup hole.

### 6. Pickup fairness and preservation

Keep both spawn queues and the single-flight launch path. When both eligible
lanes stay backlogged, alternate successful reservation/launch opportunities;
internal gets the first opportunity after idle. If the preferred lane cannot
proceed, try the other permitted lane. Do not consume a public fairness turn
when public work is deferred or admission-denied. This is a small lane preference
in the existing Manager acquisition hook, not a new scheduler. Controls and
already-owned launch completions retain their existing priority.

For a request with finitely many predecessors in an eligible FIFO source queue,
recurring service capacity and this bounded lane fairness imply eventual
reservation. Apply the same non-starvation check to service cycle/shared-input
hooks; a stream of discovery/probe work must not monopolize actual work turns.

Remove indiscriminate stale-internal-reservation deletion. Pipeline bootstrap
submits each child once into `weft.spawn.internal`; losing its sole reservation
before launch is not an allowed exception. Keep unresolved exact requests visible
under existing reserved recovery policy. Do not blindly requeue after a possible
launch: process launch and broker acknowledgement are not one atomic operation.
A new owner may not infer 'never launched' merely from a dead manager. Retention
or recovery must use existing outcome evidence and preserve uncertainty.

### 7. Make existing maintenance effects safe during overlap

This is a concrete prerequisite, not a new election algorithm. Existing duplicate
KILL already permits an overlap window. Two PostgreSQL TaskMonitors can currently
fetch the same collation row, merge separately, then let an older running snapshot
overwrite newer terminal evidence. The installed PostgreSQL `begin_immediate()`
is plain `BEGIN`; SQLite's writer serialization does not carry across backends.

Use the existing `MonitorStore._sidecar_session(transaction=True)` boundary to
serialize short PostgreSQL Monitor-store write transactions with a
transaction-scoped advisory lock for the actual database/schema and Monitor-table
domain. SQLite keeps its existing transaction. Cover all store write/schema
paths at that boundary. This is datastore mutation protection, not service
leadership: never hold it across an entire cycle, probes, external callbacks,
file output, or broker operations on other connections.

Under that boundary, make both `set_checkpoint` and the ingestion checkpoint
write advance to `max(current, supplied)`. Guard delayed summary/disposition
marks with the candidate's observed `last_message_id` and relevant eligibility
facts; if they changed, retry instead of committing stale cleanup authority.
No new durable generation/version column is needed. Preserve same-TID terminal
monotonicity and exact-ID raw-message deletion. Require deterministic two-connection
races for merge, checkpoint, and stale disposition before claiming overlap safe.

The external JSONL writer is a separate shared-resource boundary. At-least-once
reports and deterministic report IDs remain as specified; do not add exactly-once
reporting. Serialize same-path append/rotation across processes in the existing
writer's file-critical section, with a stable sibling lock file and OS advisory
file locking on supported filesystems. Reopen/check the target under that lock
so rotation cannot leave another writer appending to an obsolete inode. Keep
this lock local to file operations, never service ownership or a Monitor-store
transaction. Failure to acquire/write follows existing external-sink failure
handling and must not grant raw deletion permission. No new third-party lock
package. Keep POSIX OS-lock details inside the file writer; unsupported locking
follows the explicit sink-unavailable path rather than pretending safe output.
Test two processes through rotation and preserved complete JSONL rows.

LivenessMonitor already checks token, generation, and exact mapping message ID
before deleting; retain those guards and test overlapping probes. A delete of
an old exact row must not remove a newer replacement.

## Exact Guarantee and Policy Exception Ledger

The promise is: every eligible available shared-source item eventually receives
an acquisition opportunity and reservation; every desired, launch-permitted
service eventually has a ready owner; and after membership/failures settle,
every service key converges on one preferred owner of future shared work.
No accepted durable work is silently deleted by convergence. Successful execution
of arbitrary user code and automatic replay of uncertain effects are different
promises.

Operating assumptions are a reachable functioning broker, fair progressing
reactors, an available launcher/supervisor when startup is required, an eventual
finite bound on control/operation delay, and recurring permitted execution
capacity. Healthy presence must eventually remain continuously discoverable
under the existing refresh/expiry policy; occasional sightings alone do not
prove stable convergence. Permanent infrastructure failure is not a policy
exception and must be reported as inability to progress.

| ID | Exact existing rule and source | Work affected; outcome; required action |
| --- | --- | --- |
| E1 | `reserved_policy_on_stop="keep"` / `reserved_policy_on_error="keep"` (defaults), [TS-1.1], [QUEUE.6], [MF-6], Failure Recovery Flow; Manager `_apply_spawn_reserved_policy`. | An interrupted or failed exact reservation may remain in `.reserved`. It is preserved, not automatically retried. Inspect task/launch evidence and intentionally move or dispose of that exact message. This exception does not permit deleting it. |
| E2 | [MF-6] allows unknown launch outcome/manual recovery after reservation and exhausted recovery. | A crash between launching a process and deleting its reserved request leaves an uncertain side effect. Do not claim either no launch or safe automatic replay without evidence. Surface the TID/reserved location for reconciliation. No exactly-once launch claim. |
| E3a | STOP/KILL/PAUSE/RESUME: [MF-3], [MANAGER.10]; `reserved_policy_on_stop` / `reserved_policy_on_error`: [TS-1.1]. | PAUSE deliberately suspends that task until RESUME; STOP/KILL end it. `clear` explicitly discards reservations at the applicable disposition point. These are user-selected actions, not convergence failure. Resume or submit/recover work explicitly as appropriate. |
| E3b | Admission: [MA-1.8]. Public admission requires `used < public_limit`, internal requires `used < internal_limit`; observation failure denies admission. `N <= reserve` disables public intake. | Queued work stays unreserved while denied. Release capacity, repair the observation failure, or change the configured limit. Permanent denial cannot coexist with a pickup guarantee. No queue deletion or bypass of the limit is authorized. |
| E3c | Autostart lifecycle: [TS-1.2]. `mode=once` queues at most once per manager lifecycle; `max_restarts` bounds successful ensure restarts; `backoff_seconds * 2^(N-1)` delays restart N. | A consumed once declaration or exhausted restart budget intentionally prevents another launch. Failed queue writes do not consume the launch. Finite backoff delays eligibility. The operator must change policy or explicitly relaunch; this plan does not create global once-only/restart accounting. |
| E4 | [MF-3.2]: heartbeat registrations are runtime-only, disappear on service/Weft restart, and coalesce missed ticks rather than replay them. Existing duplicate heartbeat exit also discards its in-memory registrations. | The upsert command and its future ticks are different work. Once the command is consumed, future emissions are best-effort runtime timers, not durable scheduled jobs. Owner replacement can lose timers; callers must upsert again. On its own confirmed withdrawal the old owner stops emitting and clears timers; transient overlap before that observation is permitted, indefinite old emission after convergence is not. No lossless timer transfer or missed-tick replay is promised. |
| E5 | Named endpoints: [MF-3.1] explicitly resolves an ordinary inbox/control address; missing resolution fails and does not auto-spawn or redirect work. TIDs identify particular tasks. | An ordinary endpoint/direct-TID write is addressed to that task, not a promise that a successor inherits the private mailbox. After task exit, inspect its queue and resubmit or recover deliberately. Automatic convergence must not abandon a live owner's queued addressed work. Shared service-input queues remain subject to the pickup guarantee; Heartbeat now uses one. |

E4 applies to future ticks after a registration was consumed, not to unprocessed
mutations in `weft.heartbeat.requests`. Those remain durable shared-source work.
E5 preserves the documented addressed API, rather than quietly promising new
cross-instance delivery. Public helpers that claim logical shared-service
submission must use a shared input queue; ordinary task addressing stays explicit.

Strict internal starvation, permanent `ensure` uncertainty, and deletion of an
accepted pipeline child are deliberately absent from the exception list: this
plan changes those behaviors to meet the stated progress/preservation contract.

## Why the Algorithm Converges and Work Is Picked Up

After the operating assumptions hold, replacement discovery windows eventually
cover successful startup/readiness, so policy-allowed replacements stop multiplying.
For each resulting finite candidate set, the adaptive probe budget eventually
exceeds the finite round-trip bound. Fair probe scheduling and retention of the
preceding positive proof during renewal prevent periodic false withdrawal.
Stopped/excluded owners cannot hold readiness indefinitely. Let M be the lowest
ready, non-excluded TID for a service key. Every other owner eventually observes
and proves M and defers future shared work. M cannot defer to a higher TID.
Dependencies strictly decrease numeric TID, so a cycle is impossible.

If M fails, positive proof clears after a finite renewal attempt. A remaining
owner resumes or a supervisor starts a policy-permitted replacement. No automatic
notice permanently binds a survivor to failed M. Once failures/arrivals settle,
the same argument applies to the new minimum. All services use this argument;
service-specific owned-work disposition does not change the ordering.

A preferred manager with progressing launch capacity offers each backlogged lane
a bounded opportunity. FIFO ordering and finitely many predecessors give every
eligible source request eventual reservation. Owned reservations remain owned
through convergence; exact deletion follows successful launch or explicit
policy, never merely another owner's preference. Service cycles retain fair
work turns alongside control/probe turns. Thus convergence neither discards
accepted work nor creates a permanent gate on otherwise eligible available work.

This is eventual convergence, not strict exclusion during arbitrary partitions.
Two owners can overlap before evidence settles. Destructive service operations
retain exact-ID, terminal-proof, and custody checks, with the concrete store/file
serialization above. Arbitrary autostart programs may have their own external
side effects; existing running invocations remain owned, and this plan does not
make their application logic single-instance under partitions. If another service
requires strict one-process execution for correctness, stop and resolve it
explicitly; PING-based election cannot supply a distributed lock. Do not hide it behind a claim of 100%.

## Promotion Map

| Spec boundary | Change to make atomically with code |
| --- | --- |
| `03-Manager_Architecture.md` [MA-1.7] | Own the common facts/readiness/selection algorithm and the desired-lifecycle distinction. Replace runtime-only readiness, indefinite uncertain-owner veto, and automatic duplicate KILL. Include owned-work dispositions and guarantee limits. |
| Same file [MA-1.4], [MA-3], registry custody/current leadership-view prose | Manager uses the common algorithm; remove reversible/irreversible convergence drains, namespace-specific startup exception, all-status notice expiry, and heartbeat race repair contract. Keep actual STOP and existing scope. |
| Same file [MA-1.1], [MA-1.5], [MA-1.8] | Replace strict internal priority with bounded fairness; preserve owned-work lifetime and capacity policy. |
| `07-System_Invariants.md` [MANAGER.3], [MANAGER.8], [MANAGER.8a], [MANAGER.9]-[MANAGER.16] | One per-service decision, advisory acquisition preference, reservation authority, real STOP fence, lifecycle-policy inputs, and bounded lane opportunity. Remove contradictory duplicate-kill, permanent-uncertainty, and strict-priority wording. |
| Same file [OBS.13.6], [OBS.13.7], [OBS.15], [QUEUE.6] | Binding intent/owned work remain protected; unselected but live supervising owners are not dead. Preserve KEEP/CLEAR and terminal-proof cleanup boundaries. |
| `05-Message_Flow_and_State.md` [MF-3.1], [MF-3.2], [MF-5], [MF-6], [MF-7], Cleanup Boundary and Current Queue Lifecycle Management | Service-backed endpoint selection shares the decision; add the stable heartbeat work inbox and retain explicit timer volatility/coalescing; document short store/file mutation serialization and monotonic cleanup evidence; align registry custody, fair dispatch/bootstrap, and preservation of stale internal reservations. Keep manual uncertain-launch recovery explicit. |
| `01-Core_Components.md` [CC-2.2], [CC-2.3] | Document authorized common task participation and per-service acquisition/owned-work boundaries; ServiceTask remains mechanics rather than the exclusive membership base. |
| `00-Quick_Reference.md` queue catalogue; `04-SimpleBroker_Integration.md` [SB-0.1] | Add `weft.heartbeat.requests` as ordinary durable work, with task-owned reservations and normal dump inclusion. |
| `04-SimpleBroker_Integration.md` [SB-0.4a] | Strengthen the existing Monitor-store transaction boundary on PostgreSQL and document monotonic/conditional updates; no new store schema or service election lock. |
| `10-CLI_Interface.md` [CLI-1.1.2] | Preserve STOP-plus-notice replacement and stop confirmation. Explain that a superseded owner may still supervise existing work. |

Add plan backlinks and update adjacent implementation mappings plus owning code
docstrings in the same change. Do not create repeated full algorithms in the
spec corpus. Record the exact promotion baseline before code cites new behavior.

## Tasks

1. **Lock the shared contract against all current rules.** Verify the exception
   ledger and each participant's work boundary against code/specs. Add minimal
   counterexamples for internal starvation, uncertain `ensure`, lost pipeline
   reservation, heartbeat mutation routing, and renewal gaps. Review the shared
   contract independently before implementing. Use atomic promotion; intermediate
   task checkpoints are not independently deployable.
2. **Implement common facts, readiness, and decision.** Consolidate existing
   folds/selection/probes in `service_convergence.py` and `control_probe.py`.
   Wire authorized service participation through BaseTask and existing reactor
   scheduling; keep unkeyed tasks untouched. Add owner presence refresh and
   protected-intent compaction. Update supervisor, runtime, and service-backed
   endpoint readers together. Bind Heartbeat to the stable work inbox and update
   its helper/endpoint publication; preserve queued mutations across withdrawal. Verify that the same evidence gives
   the same decision.
3. **Apply the shared decision at each work boundary.** Migrate Manager,
   Heartbeat, TaskMonitor, LivenessMonitor, Consumer/autostart/pipeline service
   participation. Replace automatic destructive duplicate convergence and
   manager-specific drains. Keep desired-lifecycle policy around common owner
   selection, with bounded unavailability instead of permanent uncertainty.
   Review actual in-flight work and endpoint routing before accepting this slice.
4. **Close pickup and cleanup holes.** Add bounded lane fairness; preserve
   unresolved internal reservations, including pipeline children. Update runtime
   pruning, participant compaction, TaskMonitor destruction protection, and
   command read models. Prove STOP/notice order does not change STOP behavior.
   Implement the concrete short-transaction, checkpoint, disposition, and same-path
   file-writer overlap fixes above. Run real queue/process failure and namespace
   scenarios.
5. **Delete replaced mechanisms and reconcile traceability.** Remove manager
   leadership-drain/revalidation/resume helpers and exclusive fields; heartbeat's
   independent endpoint election; duplicate-service KILL/force-kill convergence;
   separate managed-service live-owner selection and divergent PONG gates;
   registration lower-manager suppression; heartbeat delete/recreate repair;
   namespace-only bootstrap branches. Preserve any helper still needed by actual
   explicit STOP. Record the removed mechanisms and verify there is one remaining
   policy owner. Append a clarification to the May 20 replacement-drain lesson:
   preserve its
   no-cancellation principle, replacing its obsolete non-stopping-drain mechanism
   with ordinary-reactor deferral; actual explicit STOP still stops children.
   Complete final independent review and required checks.

Do not add a new coordination daemon, coordination queue, lease, epoch, vote, global election lock,
plugin framework, or general scheduler. Shared code is justified by existing
policy duplication; do not build extension machinery for hypothetical services.

## Verification and Gates

Use real broker queues, keyed exchanges, reactors, and child execution for the
important proof. Reuse WeftTestHarness resource tracking and reactor_driver for
bounded progress/diagnostics. Its deduplicating foreground helper is not a
three-process test launcher. Mock only external OS observations or clocks in
focused unit tests. Do not add production test hooks.

Required firing cases:

- All startup orders for three owners, parameterized across service types;
  same decisions from supervisor, participant, and service-backed endpoint.
- Real Docker PONG despite PID absence/collision; unrelated live PID without
  PONG never wins; wrong TID/request/role/routes and paused/stopping replies fail.
- Multiple proof renewals competing for start slots; heartbeat refresh during
  a probe; replies slower than the initial timeout; dead low TID cannot starve
  another owner. Verify no periodic reopening of intake after stabilization.
- Lowest owner failure before and after a notice; paused owner resumes; a
  persistent user child and an in-flight service cycle retain their results.
- Sustained internal load plus queued public requests: every finite-prefix
  public request is launched. Test capacity denial separately as E3.
- `ensure` with an unresponsive former owner eventually launches a permitted
  replacement; `once`, exhausted restart budget, backoff, and known pending
  source request retain their precise policy behavior.
- Manager dies after reserving a pipeline child before launch: sole request
  remains recoverable. Crash after launch/before ack remains visibly uncertain;
  no speculative second launch or destructive cleanup.
- Automatic/explicit notice and heartbeat interleavings; pause beyond 300s;
  self-compaction plus peer prune; explicit intent and unresolved owned work
  remain protected. STOP before/after notice has the same actual drain outcome.
- Heartbeat H2 accepts registration X, lower H1 appears, then upsert/cancel X:
  no indefinitely surviving old emitter; volatility is visible as specified;
  unprocessed durable mutations are not silently discarded. Distinguish a
  consumed registration from its future best-effort ticks.
- Private-inbox service helper racing withdrawal: accepted input remains
  handled or explicitly recoverable; endpoint reselection alone is not the proof.
- Real TaskMonitor/LivenessMonitor overlap during uncertainty: existing exact
  deletion, transaction and custody safeguards preserve data and owned results.
- Distinct brokers/service keys and ordinary endpoint/user tasks remain separate.

A small finite-state test may supplement these with owner order, proof state,
policy eligibility, two lanes, and queued/reserved/launched-unacknowledged work.
Check safety and fair-schedule progress separately. A model is not evidence that
the production reactor or Docker path has been exercised.

Actual namespace acceptance: use test-owned containers with separate PID
namespaces, one disposable PostgreSQL database/schema and identical broker
configuration. Verify the namespaces rather than assuming containerization.
Exercise managers and at least Heartbeat plus a maintenance service through the
same algorithm. Vary local task roots without changing service key. Do not use
`--pid=host`; clean up only test-owned resources. `bin/pytest-pg` alone runs host
pytest against PostgreSQL and does not prove namespace behavior.

Use `. ./.envrc` and repository virtualenv binaries. Run focused changed tests
in the manager, managed-service, control, endpoint, heartbeat, monitor, pipeline,
and pruning areas, then the corresponding PostgreSQL suite and actual namespace
scenario. Record their concrete commands when the test slices exist. Final gates:

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q -n 0
./.venv/bin/python -m pytest
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
```

Stop and re-plan if a service needs strict distributed exclusion, accepted
private-input work has no safe disposition, heartbeat volatility is insufficient,
shared readiness cannot preserve overlap safety, or implementation needs a new
coordination authority. Do not paper over a failed guarantee by adding another
undocumented exception. Each meaningful implementation slice receives independent
review; final review must inspect the real acquisition and cleanup paths.

## Rollout and Rollback

Do not promise mixed-version convergence. Older code interprets automatic notices
and uncertain owners differently and can prune intent or kill duplicates.
Upgrade every participating manager/service and registry cleanup process sharing
the scope, including supervised restarts/container images, in one maintenance
interval. No broker key migration is needed.

Pause submissions and automatic restarts. Allow user work to finish or obtain
explicit operator instructions for persistent work; never silently kill it for
an upgrade. Stop old participants with existing controls, upgrade, verify common
selection and pickup, then restore submissions/restarts. Keep queued work and
history. After old heartbeat writers/owners are quiescent, exact-move unreserved
mutations from their verified old heartbeat inboxes to `weft.heartbeat.requests`
using ordinary broker moves. Handle reserved mutations under E1/E2, never a bulk
queue purge. Consumed volatile timers require caller re-registration under E4.
Rollback requires the same quiescence and inspection of retained intent;
no blanket registry purge or overlapping old/new restart loop. If quiescence
cannot be obtained, defer rollout rather than add a compatibility protocol here.

For rollback of the heartbeat inbox change, first prove one legacy heartbeat
ready, then exact-move unreserved `weft.heartbeat.requests` mutations to its
verified inbox before restoring writers. If no legacy recipient is ready,
retain the shared queue and report rollback incomplete; never purge it. Reserved
ambiguity remains E1/E2. This is work migration during quiescence, not a runtime
forwarding path.

## Independent Review and Fresh-Eyes Review

Review against actual code, not generic design patterns. Required verdict:
implementable and no unexplained capability loss. Challenge convergence,
renewal scheduling, every participant's work disposition, explicit exceptions,
and overlap safety. Verify each finding and choose its smallest sufficient fix.

The initial manager-only draft was superseded in scope during planning by the
owner's requirement for one algorithm for all services. Runtime review exposed
renewal-slot gaps and missing continuous-discoverability assumptions; both are
explicit above. Further service review exposed heartbeat volatility/routing,
strict-priority starvation, indefinite uncertainty, and pipeline reservation
loss. Those findings define this revised scope.

Final focused independent reviews on 2026-09-09 passed for the service runtime
and maintenance overlap prerequisites. Runtime review checked the stable heartbeat
inbox, volatile-timer boundary, addressed-input policy, trusted participant
handoff, concrete acquisition hooks, and discovery budgets carried across
replacement TIDs. Its reminder to gate LivenessMonitor reconciliation as well as
probe scheduling is incorporated above. Maintenance review checked short store
transactions, monotonic checkpoints, conditional disposition, file locking and
rotation, and existing exact-row liveness guards.

An earlier different-family (Claude) review passed an earlier shared-service
revision. The attempted final delta review failed with an API connection error;
it is not evidence for the later changes. The focused independent reviews above
cover those deltas. These verdicts assess the plan, not running code.

Document verification: the plan metadata and spec hygiene suites passed (8 tests).
Runtime, PostgreSQL concurrency, and actual namespace acceptance remain required
during implementation. The plan and its index entry remain uncommitted drafts.

Fresh-eyes questions: which existing rules change, why did they exist, why is
each replacement better, what code disappears, what remains owned, what is an
explicit policy exception, and what exact test would disprove either guarantee?

## Deviation Log

| Spec ref | Planned behavior | Current behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |
| [MA-1.4], [MA-1.7], [MANAGER.8]-[MANAGER.16] | Shared readiness/convergence; bounded uncertainty and lane fairness | Several elections, permanent uncertainty, absolute internal priority | Consistent service behavior and eventual pickup | Proposed Spec Delta; atomic promotion pending implementation |
| [MF-5], [MF-6], [OBS.13.6], [OBS.13.7] | Preserve binding intent and unresolved exact launch work | All-status expiry and indiscriminate stale internal-reservation deletion | Prevent resurrection and silent accepted-work loss | Proposed Spec Delta; atomic promotion pending implementation |
| [MF-3.1], [MF-3.2] | Service-backed routing shares owner selection; retain explicit volatile-timer contract | Endpoint and service selection can disagree | Namespace consistency without inventing durable scheduling | Proposed Spec Delta; atomic promotion pending implementation |

## Out of Scope

Manager reuse flag and interactive-run migration; path/DSN alias normalization;
renaming autostart service identities; durable heartbeat scheduling; global
once-only/restart budgets; arbitrary user-code progress; automatic replay of
uncertain launch effects; generic task death-detection redesign; new deployment
or distributed-lock infrastructure. This planning task changes documents only.
