# Runtime Ownership and Test Evidence Repairs

Status: draft
Source specs: docs/specifications/06-Resource_Management.md [RM-5.1], [RM-5.2]; docs/specifications/03-Manager_Architecture.md [MA-1.6a], [MA-1.8]; docs/specifications/07-System_Invariants.md [STATE.1]–[STATE.6], [REDUCER.1]–[REDUCER.4]; docs/specifications/13-Agent_Runtime.md [AR-7]; docs/specifications/08-Testing_Strategy.md [TS-0], [TS-2]
Superseded by: none

Class: 5. Execution supervision, process and pipe ownership, and scheduler
reclamation cross runtime boundaries. The proposed spec clarifications make
these obligations explicit before implementation. The test-only repairs do not
change lifecycle transitions or provider configuration semantics.

## Goal and Scope

Repair the reproduced gaps in command supervision and cleanup, bound stale
pending-index storage under continuing family work, and strengthen the specific
tests whose counterfactual controls failed. Reuse the existing subprocess
supervisor, scheduler structures, validator, resolver, and testing isolation
patterns. This plan authorizes no implementation until independent review and
spec promotion. It does not authorize a new execution framework or a broad
test deletion campaign.

The separate [Measured Manager Dispatch Batching](./2026-10-22-bounded-concurrent-manager-launches.md)
draft owns serial-first dispatch measurements and any resulting batching
proposal. This plan's scheduler change is only local index reclamation. It must
not alter admission, family ordering, startup custody, or the number of
processes permitted to start.

## Spec Baseline and Source Documents

Plan type: implementation with spec revision.

Source baseline: `e220748f786ea72dad00fbc7672fc484e24bef0c`, including the
specifications listed in the metadata. Record the spec-promotion identifier and
exact spec diff in the execution log after Task 1. A commit SHA is preferred;
when reviewing without an intermediate commit, record the diff base and a
reproducible spec-diff artifact instead. Later compliance is against that
promotion baseline, not the pre-promotion text.

Read `docs/program-theory.md` first. Queue truth, local ownership, cohesive
execution, and the rejected alternatives constrain the repair. In particular,
the family scheduler is an ordering mechanism over observed ancestry, not a
second admission controller; worker-local source adapters are not the task's
reactor; and separate classes are not an improvement when they merely divide
shared lifecycle state. Read the agent context in its prescribed order, then:

- `docs/agent-context/runbooks/writing-plans.md`,
  `docs/agent-context/runbooks/hardening-plans.md`, and
  `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: baseline,
  ownership, proof, and independent review requirements.
- `docs/agent-context/runbooks/testing-patterns.md`: real boundary tests,
  discriminating assertions, and Pattern 12's module-local clock isolation.
- `docs/specifications/06-Resource_Management.md` [RM-5.1], [RM-5.2]: monitor
  loading errors propagate; monitoring failures have distinct best-effort
  behavior; work execution and private teardown have separate budgets.
- `docs/specifications/03-Manager_Architecture.md` [MA-1.8] and
  `docs/program-theory.md` [ALT-WT-016]: FIFO/LIFO family selection, exact-ID
  reservation, incremental indexes, and cheap contraction remain intact.
- `docs/specifications/07-System_Invariants.md` [STATE.1]–[STATE.6] and
  [REDUCER.1]–[REDUCER.4]: the lifecycle table and validator remain authoritative.
  [REDUCER.5] is terminal-handoff-specific, not a substitute for these contracts.
- `docs/specifications/13-Agent_Runtime.md` [AR-7], Current Backends,
  `provider_cli`: project settings supply executable defaults only absent an
  explicit executable; static preflight does not launch the provider.
- `docs/specifications/08-Testing_Strategy.md` [TS-0], [TS-2]: keep observable
  owner-boundary evidence and remove redundant proof only after sensitivity
  and retained obligations are established.

Historical rationale is evidence, not a competing contract:

- Commit `5c2bf4021fda0f05961a781ba08db5071c7b0f68` deliberately preserves a
  completed subprocess outcome at a timeout wake boundary. Retrieve the retired
  `docs/plans/2026-08-08-subprocess-and-docker-provider-lifecycle-refactor-plan.md`
  with `git show 396c3b85:docs/plans/2026-08-08-subprocess-and-docker-provider-lifecycle-refactor-plan.md`.
  Its cohesive supervisor and outcome-priority rationale remain relevant. R2
  must not undo that fix by declaring an exited successful root timed out.
- Commit `3ada6ccb2c3c419119c44ed4426818aa43fc4abf` and
  `HostTaskRunner.start_agent_session` already demonstrate post-spawn acquisition
  rollback. Reuse the ownership rule, not the agent wire protocol.
- Retrieve the completed provider validation plan with
  `git show 396c3b85:docs/plans/2026-04-14-provider-cli-validation-boundary-and-agent-settings-alignment-plan.md`.
  Its static-validation and project-settings boundary is the reason T2 must
  distinguish the selected executable without invoking a provider.
- [Test Audit Repairs](./2026-10-07-test-audit-repairs-plan.md) records completed
  repair slices and historical green evidence while retaining draft metadata.
  This is a narrow follow-up, not a wholesale supersession. T1 is a residual
  validator-boundary hole in the retained pair matrix; T2 concerns executable
  selection rather than the earlier no-hidden-launch proof; T3 covers remaining
  command-layer clock patches; T4 concerns the demonstrated duplicate tables.
  Preserve the earlier keepers and do not replay its broad deletion list.

## Confirmed Evidence and Current Owners

Line numbers below describe the source baseline; use the named symbols when
lines move. Temporary review probes established the failures, but implementation
acceptance requires durable regression tests, not access to `/tmp` scripts.

| ID | Reproduced behavior | Owner and existing evidence |
|---|---|---|
| R1 | A real host command that never reads a 1 MiB input remained blocked after a 0.2 s timeout and a cancellation request. After a readiness fence and a 1 s join, its supervisor was still in `stdin.write`; killing the owned child released it. | `weft/core/runners/subprocess_runner.py::run_monitored_subprocess` calls `_write_process_input` before starting its execution clock or supervision loop (142–144); `_write_process_input` writes and flushes synchronously (512–527). |
| R2 | A root command printed output and exited zero while its descendant retained stdout/stderr. With timeout 0.2 s, the runner still waited after approximately 1 s; it returned success only after the descendant was killed. | `run_monitored_subprocess` requires root exit plus both EOF flags before breaking (198–199), but timeout applies only while the root runs (203–206). `_drain_streams_until_closed` already has a 0.25 s budget, yet normal completion reaches it only after EOF. |
| R3 | A real Consumer using `monitor_class="builtins.str"` raised the required TypeError and finalized as failed/closed, but the spawned command still executed and remained alive after the drive scope finalized. | Host `Popen` at `weft/core/runners/host.py:936` transfers into `run_monitored_subprocess`; monitor loading at subprocess runner 121 occurs outside post-spawn rollback. Consumer finalization cannot recover ownership that the runner lost. |
| S1 | With two live pending entries, repeated select/remove/launch/retire/refill cycles left 100,002 physical LIFO nodes after 100,000 cycles; FIFO remained at two. At 500,000 cycles the LIFO retained 500,002 nodes. Draining the bucket released it. | `weft/core/family_scheduling.py::_PendingIndex.remove` only physically removes the current heap root or an empty bucket; arbitrary removed IDs remain until selected at the root. This is a production reducer probe, not 500,000 OS processes. |
| T1 | A mutation of the actual validator to accept `created -> timeout` made `TaskSpec.set_status` accept that edge while the 48-test lifecycle slice stayed green. | `tests/specs/taskspec/test_state_transitions.py::test_lifecycle_machine_pair_matrix_matches_transition_table` calls the validator for allowed edges but the raw machine for forbidden edges. |
| T2 | Making project-settings loading return empty defaults left the wrapper validation test green because the ambient provider executable was installed. The direct resolver keeper failed the same mutation on the selected path. | `tests/core/test_agent_validation.py::test_validate_agent_runtime_preflight_uses_project_agent_settings_for_executable`; keeper in `tests/core/test_provider_cli_backend.py::test_resolve_provider_cli_executable_resolves_relative_project_setting_against_spec_context`. |
| T3 | Running the exact deadline test body with its patch active changed SimpleBroker's clock too. A real SQLiteRunner transaction waiter with a 50 ms budget stayed blocked beyond 250 ms; the unpatched control expired. | Three tests in `tests/commands/test_task_commands.py` mutate `task_cmd.time.monotonic`, which is the shared stdlib module. SimpleBroker's transaction wait uses that same module. |
| T4 | Removing each allowed lifecycle edge was caught by both duplicate tables and the independent pair matrix. A wrong `spawning -> failed` action was caught only by the core table. | `tests/core/test_state_machines.py::test_task_lifecycle_allowed_transitions_are_covered` and `tests/specs/taskspec/test_state_transitions.py::test_lifecycle_machine_covers_allowed_state_transitions`. Preserve the core table's action assertion before removing duplicate coverage. |

The default SQLite suite at the source baseline passed 5,914 tests with 38
skips in 206.02 s. That is context for the missing assertions, not proof that
the defects are absent or a substitute for the acceptance runs below.

## Invariants and Boundaries

The runner owns acquired child resources until explicit transfer or confirmed
cleanup. A terminal TaskSpec alone is not a reaping proof. Monitor class/type or
constructor errors stay fatal and preserve their original exception; ordinary
monitor start/poll/stop failures retain their documented best-effort behavior.
Cleanup failures cannot silently replace a successful command or the primary
failure. Preserve cancellation/timeout/resource-limit precedence in the existing
supervisor, including completion observed before an unaccepted timeout.

Input delivery is part of concrete command execution. Pipe backpressure cannot
disable supervision. Root exit, pipe EOF, and adapter completion are distinct
facts: an inherited descriptor is not evidence that the root still executes.
Bounded capture must also release this runner's adapters and descriptors. Returning
while daemon threads remain stuck in buffered I/O is not a fix. The resource
monitor's psutil-visible-tree boundary remains unchanged; this plan does not
promise to kill already reparented or deliberately detached descendants.

Queue truth, reserved-policy handling, immutable `spec`/`io`, TID identity,
forward-only states, spawn-based broker isolation, and runtime-only state queues
remain unchanged. Keep the real
`TaskSpec -> Consumer -> TaskRunner -> outbox/task log` path in end-to-end proof.
Do not add a second state store, queue reader, hidden provider probe, timeout
polling cap to the steady task reactor, dependency, public flag, or new outcome
schema. Outbox output preserves all bytes/text captured before producer shutdown,
subject to existing error/shutdown delivery rules; delivery has no 250 ms cap.

For S1 preserve round-robin branches, newest eligible tip selection, independent
root FIFO, oldest sibling borrowing, cached parent contraction, exact-ID broker
reservation, and cheap heap meld. Reclamation is local derived-memory maintenance.
No broker token moves or full pending-dictionary scan may implement it. Frontier
normalization cost and general scheduling throughput tuning are outside scope.

Source-only concerns about interactive `CommandSession.send` and interactive
monitor loading are not confirmed findings in this plan. Read those paths for
reuse and difference analysis; do not silently expand the repair to them. No
generic supervisor extraction or unrelated test sweep belongs in this scope.

## Difference Register

| Existing difference | Disposition and reason |
|---|---|
| Shared command runner receives stdin in one bounded work invocation; interactive sessions accept later messages over their lifetime. | Preserve. Share a phase/ownership rule where useful, not the interactive session API or a new common framework. |
| Interactive exit notification is independent of stream EOF; shared command normal completion currently waits for EOF. | Unify root-exit handling and bounded capture. Reuse finite per-stream draining after producer shutdown, ending on empty queues rather than EOF; retain separate adapters and session behavior. |
| Cancellation/timeout/limit branches already use bounded drain, while normal completion's drain is reached too late. | Apply the same finite drain owner to normal completion and preserve each branch's accepted outcome. |
| Fatal start callbacks already use `_cleanup_after_callback_failure`; monitor construction and pipe setup do not. | Unify post-spawn rollback coverage with existing stop/kill/reap mechanics, retaining original exception identity. Avoid two competing cleanup owners. |
| Monitor loading/type errors propagate; ordinary monitor start/poll/stop errors are logged and tolerated. | Preserve. A blanket catch-and-default monitor would conceal configuration errors. |
| Host and Docker share the subprocess supervisor, but have distinct runtime stop/kill hooks and acquisition context. | Preserve hooks and Docker outer cleanup. The shared supervisor must not bypass container cleanup by treating its CLI child as the whole runtime. |
| Current readers normalize CRLF, split multibyte input, and signal EOF through queues; callbacks have established error handling. | Preserve encoding, error, newline, callback, and EOF semantics while making adapter cancellation and ownership explicit. |
| LIFO uses a meldable leftist heap with lazy stale entries; family FIFO already supports exact indexed removal. | Preserve both selection structures. Add bounded bucket-local reclamation to LIFO; do not replace cheap meld with whole live-set unions. |
| Core lifecycle table checks action, transition ID and target; spec table checks ID/target but lacks a per-edge action oracle. | Consolidate into one literal action/ID/target table through the validator only after preserving the stronger assertions. |
| Pair matrix is an independent allowed-pair oracle; `TaskSpec.set_status` additionally owns same-state and terminal rules. | Preserve distinct proofs. Forbidden pairs must exercise the actual validator; TaskSpec integration proves its own boundary. |
| Provider wrapper proves settings reach preflight; resolver test proves relative path resolution. | Preserve both, make the wrapper discriminate selected executable with the real resolver intact. |

## Proposed Spec Delta

Promotion strategy A: edit normative prose in the existing files first; do not
claim the new implementation or tests already exist. Add plan backlinks during
promotion, then update nearby implementation mappings and code docstrings with
the verified owners in Tasks 2–5. These are insertions; no existing clause is
deleted. Testing requirements already exist, so T1–T4 need no new normative rule.

### Resource Management [RM-5.2]

Insert after the opening paragraph ending “used inside the runtime.”:

> For one-shot command execution through the shared subprocess supervisor,
> delivering the work item's stdin is part of execution. The execution clock
> starts no later than input delivery, and input backpressure must not prevent
> cancellation, timeout, or resource-limit supervision. Closing input is owned
> by its I/O adapter; cancellation must not depend on another thread acquiring
> a buffered I/O lock held by a blocked operation.
>
> Root process completion and output-pipe EOF are separate observations. Once
> root completion is accepted, the supervisor uses the bounded private
> stream-drain budget to await output EOF and finish accepting output from
> inherited pipes. When both output adapters finish or that budget expires, it
> stops and joins its owned I/O adapters and closes their owned endpoints. This
> freezes a finite set of captured output. The supervisor delivers that captured
> output before forming its returned outcome; expiry must not discard output
> already captured in its local queues. The same capture, shutdown and delivery
> sequence follows the existing runtime stop/kill hooks for cancellation, timeout
> and resource-limit outcomes, preserving those outcomes.
>
> The capture budget is not a total result-delivery or teardown timeout. Delivery
> duration depends on captured volume and sink throughput and may exceed the
> private budget. Output produced after capture is stopped is not promised.
> Budget expiry neither converts a completed root into an execution timeout nor
> asserts physical EOF. Missing physical EOF remains a bounded diagnostic, with
> no new terminal state or outcome field.
>
> While execution is active, output drainage performs finite work per stream
> before returning to other streams and supervision. After producer shutdown,
> delivery repeatedly drains the finite queues without idle waits. These rules
> do not preempt an arbitrarily blocking callback. A callback exception follows
> the existing execution-failure cleanup and exception-precedence contract; it
> does not guarantee delivery to the failed callback. Existing Consumer shutdown
> rules still govern a publisher that has stopped accepting worker events.

### Manager terminal retry [MA-1.6a] (S2 clarification)

Strategy A: before the S2 owner repair, insert after the sentence ending
“existing reactor timeout path.” in `03-Manager_Architecture.md` [MA-1.6a]:

> Cleanup retires a due publication-retry deadline when servicing it, so any
> proof-grace interval that then starts becomes the active deadline; another
> failed publication installs a fresh retry.

This makes the existing no-expired-grace-spin contract explicit. It does not
require a grace interval after every retry or alter custody/publication policy.

### Resource Management [RM-5.1]

Insert immediately before “Enforcement mechanics and security boundary:”:

> After successful process creation, the one-shot command runner owns rollback
> for later acquisition failures, including monitor loading and pipe-adapter
> setup. Before propagating such a failure, it stops and reaps the acquired root
> process and releases its started adapters and endpoints using the runtime's
> existing stop/kill hooks. The original failure remains primary. Monitor-class
> validation and constructor failures are not converted into successful
> execution without monitoring. This obligation does not broaden the
> psutil-visible process-tree enforcement boundary below.

### Manager Architecture [MA-1.8]

Insert after the paragraph beginning “Classification advances in bounded pages”
and ending “fixed FIFO visibility window is introduced.”:

> Derived pending indexes must not retain unbounded selection history while
> their live pending population stays bounded. Stale-entry reclamation must
> preserve selection order and cheap parent-bucket meld, operate on the affected
> bucket rather than scanning unrelated pending work, and leave broker custody
> unchanged. A bucket-local linear compaction is permitted with an amortized
> bound and measured latency evidence; this is not permission for a full pending
> graph or dictionary scan on each dispatch or exit.

## Comprehension Gate

Before implementation, record answers in the execution log, then compare with
the expected answers. A mismatch blocks edits until the cited owner is reread.

1. Why can an exited root still leave the runner blocked, and why is setting
   status to timeout an incorrect repair? Expected: descendants can own pipe
   writers independently of the root; completion already observed has priority
   over an unaccepted timeout. Bound stream capture and preserve root status.
2. Why is “put stdin on a thread, then close it in `finally`” insufficient?
   Expected: a buffered write can hold its lock, so cross-thread close can block;
   the adapter needs a cancellable I/O primitive and must own close and join.
3. Why can monitor loading fail after launch and still be a runner bug?
   Expected: the validation error should propagate, but the runner already owns
   the live child. Consumer terminal publication cannot reap a forgotten handle.
4. Why not use arbitrary deletion with a parent pointer in the leftist heap?
   Expected: leftist rank bounds its right spine, not arbitrary ancestor depth;
   rank repair along a left spine need not be logarithmic. Use proven local
   reclamation bounds instead of asserting an unsupported deletion bound.
5. Which test should reject an invalid transition, and which should distinguish
   a wrong lifecycle action? Expected: the independent pair matrix calls the
   real validator on forbidden pairs; the retained literal edge table checks
   expected action/ID/target. TaskSpec separately owns idempotent nonterminal
   updates and rejects terminal self-transitions.

## Implementation Tasks

### 1. Promote contracts and resolve the pipe primitive gate

Files to modify: the two spec files in Proposed Spec Delta,
`docs/plans/README.md`, and this plan's execution record. Read
`weft/core/runners/subprocess_runner.py`, `weft/core/runners/host.py`,
`weft/core/tasks/interactive.py`, `weft/core/tasks/sessions.py`,
`extensions/weft_docker/weft_docker/plugin.py`, and `weft/_constants.py` first.
The host command and Docker command paths call the same supervisor. Existing
interactive process-exit notification and agent-session acquisition rollback
are reference implementations, not drop-in replacements.

After independent plan/spec review, apply the exact insertions and record the
promotion baseline. Before production edits, run a small disposable pipe probe
on the available local Python/platform combination and inspect the supported
platform primitives. Per owner direction on 2026-10-27, Windows execution is a
required CI gate after local implementation, not a prerequisite to local edits. Preferred implementation: private
owned adapters in the existing subprocess module use raw nonblocking pipe I/O,
retain decoder/newline state, and use an adapter stop event with the existing
named worker-local wait floor when the pipe would block. Each adapter closes its
own endpoint; the supervisor requests stop and joins. Establish actual supported
`os.set_blocking`/pipe semantics, including Windows, from the supported Python
documentation/source and a platform test in CI; do not infer portability from
POSIX success. Monitor the Windows CI jobs and repair failures before completion.

Verify split UTF-8, split CRLF, lone CR, stderr separation, stdin encoding/error
handling and Windows newline translation against the current text contract.
No supervisor-side `TextIOWrapper.close()` may contend with a blocked adapter.
Evaluate standard-library `communicate(timeout=...)` mechanics before choosing
the adapter, but do not replace the supervisor with a call that loses streaming
callbacks, cooperative cancellation or resource sampling. Reuse applicable pipe
and decoder primitives, not a second supervision loop. Record primitive choice,
ownership and shutdown bounds.
Stop for a revised design/review if supported platforms cannot supply cancellable
owned pipe I/O, if closing can block, or if a general framework is emerging.
This gate permits a disposable experiment, not an unreviewed alternate product
path. Run documentation gates listed below after promotion.

### 2. Repair the shared command lifetime (R1–R3)

Files to modify: `weft/core/runners/subprocess_runner.py`,
`weft/core/runners/host.py` only for acquisition/transfer plumbing if needed,
`weft/_constants.py` only if a new named budget is justified,
`tests/core/test_subprocess_runner.py`, `tests/tasks/test_runner.py`,
`tests/tasks/test_consumer_terminal_events.py`, and the RM implementation notes.
Inspect `extensions/weft_docker/weft_docker/plugin.py` for compatibility; change
it only if the shared ownership contract requires a concrete caller adjustment.

Write RED tests with real short Python child programs and readiness files/events.
Avoid sleep-as-readiness and host tools other than the repo Python. Test setup
may inject a failing monitor class, but must not fake `Popen`, child liveness,
the supervision loop, Consumer finalization, or broker queues. Register precise
PID/create-time cleanup immediately; cleanup must run even on RED assertion
failure and must never kill unrelated processes.

- R1: child signals ready and never reads input larger than pipe capacity. Test
  timeout and cancellation separately, plus an ordinary successful large-input
  round trip. Assert bounded supervisor completion, expected outcome, root
  reaping, joined input/output adapters and closed owned descriptors. The timeout
  clock must include delivery, not start after it. Do not patch global clocks.
- R2: root emits known stdout/stderr and exits zero; a controlled descendant
  holds inherited pipes open past the private drain budget. Assert captured
  output, successful root outcome, finite return, diagnostic and released
  adapter resources while the descendant is still held. Release that descendant
  in test cleanup. Add a descendant that continuously produces output, not just
  one silently holding descriptors; the same capture/resource bound must hold,
  with total delivery allowed to scale with the finite captured volume.
  Repeat the root-outcome assertion with nonzero exit and
  preserve the existing timeout-wake-boundary test. Finite process-test safety
  deadlines are generous cleanup bounds, not assertions of exact scheduling.
- R3: a real Consumer starts a child then monitor type validation or construction
  fails. Assert the original exception, failed task-log/terminal outcome, correct
  reserved-policy behavior and no live owned root after drive-scope finalization.
  Add focused setup-failure checks after partial adapter acquisition. Keep the
  existing callback/monitor cleanup failure tests to prove exception identity
  and best-effort behavior were not replaced.

Start the execution clock before input delivery, keep cancellation/timeout/limit
checks live, and accept root completion independently from EOF. Keep finite
per-stream work in the running loop. After accepting root completion or invoking
the terminal stop/kill hook, use the existing output adapter threads' joins with
one shared absolute SUBPROCESS_STREAM_DRAIN_TIMEOUT deadline to await capture
completion. Then stop all acquired adapters before joining any, close owned
endpoints, check finalized adapter errors before callbacks, and drain the
now-finite captured queues completely without sleeps or a delivery deadline.
Terminate this frozen phase when both queues are empty, not when EOF flags are
set; inherited pipes may never produce physical EOF. Reuse the stop/join/close owner in finally; handle partial
acquisition and preserve primary exceptions. Do not create a new watcher,
output store, deadline thread or synthetic physical EOF marker.

Consumer's existing per-work-item callback closures track actual final callbacks.
After a successful live command outcome, publish a missing logical final for
each still-open stream through the same event publisher before the successful
work result. Preserve chunk indices, stdout strip transform and exactly-once
finals. Publish any missing stderr final before the missing stdout final, since
the CLI result reader stops at the latter. A returned supervisor outcome implies
all collected chunks have reached their callbacks; callback failure propagates
instead. Land captured-backlog preservation and the logical seal together. Do not change result readers, add an outcome field, or unconditionally
publish finals during failure unwinding. Non-ok outcome settlement stays as-is.

Keep the deterministic real-queue finite-batch control and its mutation proof.
Replace the old final-delivery-deadline test because its contract was superseded,
not because it was tautological. Add captured-backlog delivery with a controlled
slow sink and real process; preserve exact data and genuine EOF. Add real
Consumer/public-result empty/nonempty inherited-pipe cases and exact large-result
evidence. Removing frozen-backlog delivery or the Consumer logical seal must
recreate the respective missing-output assertion. Cover cancellation, timeout,
limit outcomes and callback failure during final delivery with owned-resource
cleanup. Keep sustained-output fairness and controlled descendant tests; assert
finite capture and producer shutdown rather than a 250 ms total delivery bound.
Adapt `test_stream_reader_normalizes_crlf_split_across_chunks`
to preserve normalized text and EOF through a decoder-level or real-pipe proof;
its fake stream without `fileno` and exact chunk splits are not public adapter
requirements. Do not add production fake-stream support or freeze OS read
chunk boundaries to keep that fixture. Reuse
`_stop_process_runtime` and `_cleanup_after_callback_failure` stop/kill/reap
mechanics inside one explicit acquisition/rollback owner. Name any helper to
match its expanded responsibility. Keep stdout/stderr queues as existing local
transport, preserve callback semantics, and stop adapters before joining them.

The runtime hook remains responsible for its runtime. On Docker, preserve
container stop/kill cleanup in addition to reaping the local CLI child. Fatal
configuration errors cannot be relabeled as successful unmonitored execution.
Do not add synchronous Consumer cleanup as a second owner of the same child.
Stop and review if any test can pass by returning with blocked daemon readers,
if teardown changes an accepted outcome, or if resource-limit/control precedence
changes. Run the focused runtime and Docker gates before continuing.

### 3. Repair derived scheduler bookkeeping (S1–S2)

Files to modify: `weft/core/family_scheduling.py`,
`tests/core/test_family_scheduling.py`, and the [MA-1.8] implementation note.
Read `tests/core/test_manager_family_maintenance.py`,
`tests/system/test_family_scheduling.py`, and Manager's calls to
`remove_pending`, `launched`, and `retire` first. Keep Manager's broker
reservation and admission paths unchanged.

Add physical-node count H alongside the existing live count L on `_PendingIndex`.
Maintain H <= 2L at public scheduler-operation boundaries; empty buckets have
H = 0. A non-root removal may leave a tombstone, but when H > 2L, iteratively
traverse only that bucket's physical heap and retain each currently owned live
ID once, then rebuild with bottom-up pairwise `_meld`. A node is live for this
bucket only if its ID is in `FamilyScheduler._pending` AND its current
`_request_indexes[id].owner()` is the bucket being rebuilt. A globally pending
ID can have been removed and re-added into another bucket after late ancestry
recovery; global membership alone is insufficient. A temporary bucket-local seen
set also removes duplicate same-ID nodes and is discarded afterward. Use the
same current-ownership predicate for candidate stale-root pruning so a re-added
ID in another bucket cannot be offered through the old bucket. Reuse the existing
index-handle/owner indirection; no generation store or second liveness index.

Use dictionary lookups, not iteration over global pending/request-index maps.
`remove_pending` already removes the pending dictionary entry before index removal;
pass the existing membership and ownership views into the local operations.
Decrement H for stale-root pops. On contraction, sum H and L, meld the roots,
and transfer/reset source bookkeeping consistently with the existing owner
handle/union-parent behavior. Two buckets satisfying H <= 2L preserve that bound
under meld, so contraction itself needs no global scan or live-set union.

Document and verify the operation accounting: ordinary leftist insert/meld/pop
remain logarithmic in physical heap size; traversal and bottom-up meld rebuilding
have O(H) work, plus current-owner resolution through existing parent handles.
Account for those pointer traversals/path compression explicitly; do not label
the entire operation strict O(H) or each owner lookup constant without proof.
Since a rebuild is triggered only after more than half the stored nodes are
stale, its traversal/rebuild work is charged to discarded history. A single
compaction can still pause for O(L) rebuilding plus owner-resolution work. This plan does not claim worst-case constant or
logarithmic arbitrary deletion, nor constant Manager turn latency.

RED proof: keep a long-lived family/tip and a constant live pending population;
repeat the exact production select/remove/launched/retire/refill sequence. Assert
selected IDs against an independent small reference policy and retained node
count against the bound. Internal node counts are justified here because memory
retention is the defect; do not add a production test-only counter API. Include
root pops, arbitrary removals, emptying/refill, same-ID removal/re-addition,
unequal bucket meld, contractions with stale nodes, and old request handles
resolving through owner indirection. Add the exact late-recovery control: recover
root 100; add 200/201/202 with ancestry (100,150) and 999 under 100; remove
200/201; recover 150; re-add 200/201; remove 202. Old bucket 100 must retain only
999 (L=1), while 200/201 are owned by bucket 150. Check both reclamation bounds
and candidate ownership before/after contraction; reverting the owner predicate
must fail. Vary owner-chain depth and count pointer traversals as well as heap
visits. This is a real reducer proof, not a claimed complete Manager incident.
Retain FIFO/round-robin/ancestry tests. Count inspected nodes to rule out a hidden
scan of unrelated pending work.

Measure worst compaction and total work at 100, 1,000 and 10,000 live entries,
with unrelated backlog varied independently. Compare against the baseline and
record parameters, timings, operation counts and maximum retained nodes. This
is a design gate: if local compaction creates unacceptable control/reap latency
in a real Manager exercise, stop and review an explicitly specified incremental
reclamation design, including mutations and meld during rebuild. Do not invent
a per-dispatch global scan, a fixed family cap, or unproved exact-deletion bound
to make the local benchmark green. Run reducer and real Manager family gates.

#### S2: retire a serviced terminal-publication retry deadline

Admission B's pre-implementation review identified a prerequisite repair in the
existing Manager owner. Governing contract: `03-Manager_Architecture.md`
[MA-1.6a], the paragraph requiring failed terminal publication to retain custody
with a bounded retry and explicitly forbidding an expired-grace spin. This
restores that contract; it does not promote B's dispatch policy.

Confirmed baseline sequence: a real cleanly exited process with exact staged
reserved custody encounters named broker proof-read and failsafe-write failures.
The retry is future and respected. Once due after broker recovery, absence of
proof starts a fresh proof-grace interval, but the old expired retry remains
positive. `_manager_child_timeouts` prioritizes it and reports zero throughout
the new grace. A second valid request still dispatches in the baseline; B's
proposed deadline veto would additionally stall it. The first child's staging is
an owner-boundary test, not a claim of naturally inducing this whole sequence
through a complete first-task launch.

Files: `weft/core/manager.py`, the nearest existing terminal-publication/custody
regression in `tests/core/test_manager.py`, and the [MA-1.6a] normative
clarification plus implementation mapping. In `_cleanup_children`, after confirming exit, respecting retained
survivors and skipping a future retry, clear the consumed retry deadline before
the proof-grace check. A later failed publication must install its normal fresh
retry. Do not clear a future or unserviced deadline, shorten grace, acknowledge
custody early, add a timer snapshot/latch, change `_manager_child_timeouts` into a
second policy owner, or modify dispatch/worker counts.

Before editing production, obtain RED through a real clean exit and actual
broker failure dependencies. Verify future retry retention, due retry consumption
into a positive proof-grace timeout, exact reserved-row retention, normal second
request dispatch, later genuine failsafe publication and acknowledgement. Remove
only the deadline reset to obtain the intended timeout/custody assertion failure.
Use an owner-local clock facade, never patch stdlib time globally. Keep all
process and Manager/harness ownership explicit even on failed assertions. Probe
and test cleanup failures are not passes. Independent review must confirm this
small owner repair before admission B begins. Include it in the common repaired
baseline for all measurement arms so its effect is not attributed to B.

### 4. Repair and consolidate the demonstrated test gaps (T1–T6)

Files to modify: `tests/specs/taskspec/test_state_transitions.py`,
`tests/core/test_state_machines.py`, `tests/core/test_agent_validation.py`, and
`tests/commands/test_task_commands.py`; follow-ups T5/T6 also touch
`tests/tasks/test_runner.py` and `tests/tasks/test_consumer_terminal_events.py`.
Read the actual validator,
`weft/core/taskspec/model.py::TaskSpec.set_status`, the provider resolver and its
keeper in `tests/core/test_provider_cli_backend.py`, and the existing module-time
facade in `tests/commands/test_task_commands.py` first. No production seams or
behavior changes belong in this task.

- T1: make forbidden and allowed pairs call the real
  `validate_task_status_transition` against the independent literal oracle.
  Add or strengthen `TaskSpec.set_status` boundary proof for forbidden edges,
  nonterminal same-state idempotence and terminal self-transition rejection.
  Preserve timestamp/side-effect tests. Mutate the real validator to accept
  `created -> timeout` without bypassing imported aliases; the intended forbidden
  assertion must fail, not collection or fixture setup.
- T4: first extend the retained spec table with literal expected action, ID and
  target and route it through the validator. Prove a wrong
  `spawning -> failed` action is detected. Then remove only the duplicate core
  allowed-transition table. Re-run the edge-removal controls against the retained
  table and pair matrix. Keep generic StateMachine tests, independent pair
  oracle and TaskSpec integration; deriving expected results from production
  transitions would make this consolidation tautological.
- T2: isolate executable lookup from ambient provider installations and assert
  which configured fixture executable the wrapper selected, with the real
  resolver active. An observation seam at the wrapper's resolver boundary may
  record the real return value; it must not replace resolution. Keep a competing
  default/explicit trap so dropping settings cannot pass merely because some
  executable exists. Retain the direct relative-resolution keeper. The empty
  project-settings mutation must fail the selected-path assertion; no provider
  executable is launched.
- T3: replace shared-module attribute patches in
  `test_deadline_from_timeout_clamps_at_zero`,
  `test_deadline_helpers_share_exact_expiry_boundary`, and
  `test_await_control_surface_public_grace_outlives_expired_kill_ack` with a
  module-local `task_cmd.time` facade exposing the methods used by that test.
  Restore the patch before cleanup. Repeat the fenced real SimpleBroker
  transaction-wait control while the local fake clock is active: its own timeout
  still advances. Do not add production clock injection or broaden this into an
  unproven repository-wide clock claim.

T5 (2026-10-28 CI follow-up; fixture-only, no product/spec delta): in
`tests/tasks/test_runner.py::_write_descendant_scripts`, remove the unused
repository and collections imports from the two generated programs. They need
only stdlib process/file operations. In
`test_command_session_terminate_kills_descendants`, acquire cleanup ownership
immediately after `start_session`, before waiting for readiness. On readiness
failure, preserve the original assertion and add session return-code/stderr
context. Always clean up the owned root/tree and any pinned descendant using
existing identity-verified primitives independently of the `terminate` method
under test, then stop the monitor. Preserve the two-second readiness budget,
worker count, actual termination call and descendant-exit oracle. Governing
contract: `docs/specifications/08-Testing_Strategy.md` [TS-0] resource ownership
and meaningful test sensitivity; this does not alter command-session behavior.

The observed CI failure did not capture root status/stderr, so its cause is
unresolved. A local real-session comparison established avoidable import work
(original readiness 0.320 s; stdlib-only 0.051 s), not that imports caused the CI
failure. The failed-readiness cleanup omission is independently confirmed in
code. Before landing, demonstrate that an injected readiness failure leaks with
the old fixture but cleans up with the candidate, and that a broken terminate
still fails the descendant-exit assertion while cleanup completes. Exercise both
callers of the shared generated scripts and the canonical PG path. Review the
small fixture delta independently. Do not call a passing rerun a causal diagnosis.

T6 (2026-10-28 Windows CI follow-up; test-only): the new
`test_monitor_validation_failure_finalizes_consumer_and_reaps_command` reached
its failed state with a PermissionError on `command.pid`, before the real invalid-
monitor validator ran. The CI log confirms that failed dependency; it does not
identify the Windows sharing/permission cause. Replace the unnecessary file
readiness handshake with observation of the real Host-to-supervisor process
handoff if that boundary supplies the already-created process. Retain the actual owned `Popen` object before any observation can raise,
assert it is live, then forward unchanged to the real supervisor
and real `builtins.str` validation. This contract needs actual process acquisition,
not proof that the child's Python script has published a file. Preserve both
KEEP/CLEAR reserved-policy cases, the expected validation error, single terminal
fact, closed reactor and completed-reap assertions. Assert its recorded `returncode`
before any oracle poll: `poll()` itself can reap and conceal missing cleanup.
Use the retained `Popen` for independent failure-path kill/wait; no psutil identity
reconstruction is needed for this acquired root. Keep cleanup independent of the
owner defect. Do not patch global subprocess APIs, relax timing, repair filesystem
permissions, or replace the validator result. Governing [RM-5.1] and [TS-0]
contracts are unchanged. Verify the old missing-rollback behavior still fails
the live-child assertion, both backends pass, and the corrected test passes
Windows CI. Review this narrow test change independently before completion.

Run mutation controls in isolated subprocesses or bounded monkeypatch scopes,
never leave mutant code in the repository, and record exact failure assertions.
For T3, read the installed SimpleBroker runner and `../simplebroker` implementation
before choosing the wait-entry fence. Restore/commit the owned transaction and
join the waiter in `finally`, including when the negative control blocks.
Stop if a keeper loses an action/ID/terminal assertion, a test needs the ambient
provider installation, or a mutation fails for infrastructure reasons. Run the
focused test-repair gates.

### 5. Reconcile traceability and complete integrated verification

Files to modify: this plan, its index row, touched spec backlinks/implementation
notes, and materially changed code docstrings. Add a durable lesson only if the
repair reveals a new general rule not already in the testing/ownership guidance.
Do not edit the October 7 plan's historical results. Record the actual diff,
firing RED assertions, GREEN commands, runtime resource proof and compaction
measurements. Apply independent review after the runtime and heap slices, then
review the integrated change with the cross-family procedure below.

Run the full gates below after focused gates pass. Audit the lifecycle difference
register against the final code, including fatal cleanup paths and Docker hooks.
Review exact path/symbol references directly: `bin/check-doc-paths` deliberately
excludes plan bodies. Reconcile spec -> plan -> owner docstring/implementation
mapping in both directions; do not invent a backstitch command if no executable
is configured. Report skipped platforms/backends as unverified, not passed.
Close the plan and index together only after required review and evidence are
complete. A failed primitive or compaction design gate leaves this plan draft
with the blocking evidence and revised proposal recorded.

## Verification Commands and Acceptance

Source `. ./.envrc` first. Use the repo toolchain and configured default pytest
concurrency for core tests; do not reduce workers to hide load-sensitive failures.
The extension/integration suites retain their established `-n 0` CI recipes in
`.github/workflows/test.yml`; that is their baseline, not a reduced core workload. Commands
below are planned gates, not claimed results. New regression node IDs are chosen
when their owning files are edited, then recorded with RED/GREEN output.

```bash
./.venv/bin/python -m pytest tests/core/test_subprocess_runner.py tests/tasks/test_runner.py tests/tasks/test_consumer_terminal_events.py tests/tasks/test_command_runner_parity.py
./.venv/bin/python -m pytest -n 0 extensions/weft_docker/tests/test_docker_plugin.py extensions/weft_docker/tests/test_agent_runner.py
./.venv/bin/python -m pytest tests/core/test_family_scheduling.py tests/core/test_manager_family_maintenance.py tests/system/test_family_scheduling.py
./.venv/bin/python -m pytest tests/core/test_state_machines.py tests/specs/taskspec/test_state_transitions.py tests/taskspec/test_taskspec_properties.py tests/core/test_agent_validation.py tests/core/test_provider_cli_backend.py tests/commands/test_task_commands.py
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py
./.venv/bin/python bin/check-doc-paths
./.venv/bin/python bin/coalesce-check
./.venv/bin/python -m pytest -m '' tests
./.venv/bin/python -m pytest -n 0 integrations/weft_django/tests
./.venv/bin/python -m pytest -n 0 extensions/weft_docker/tests
./.venv/bin/python -m pytest -n 0 extensions/weft_macos_sandbox/tests
./.venv/bin/python -m pytest -n 0 extensions/weft_microsandbox/tests
./.venv/bin/python bin/pytest-pg --all
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/ruff format --check .
git diff --check
```

Run each extension recipe in its owning CI environment: macOS sandbox on macOS;
Django, Docker, and Microsandbox on their configured Linux jobs, with their declared
extras installed. Do not combine these roots into a new parallel acceptance run.
The pipe regressions and
newline/encoding controls must run on supported Windows and POSIX environments;
one local platform does not certify the other. Docker-enabled verification must
exercise its real cleanup boundary where the configured test requires Docker;
report unavailable daemon proof explicitly. PostgreSQL verifies that the
queue-visible lifecycle/selection effects still use the normal broker path;
the pure heap tests do not substitute for it.

Acceptance combines observable success and ownership: timeout/cancel returns
without a blocked stdin adapter; exited roots return their actual outcome within
private teardown bounds despite inherited descriptors; monitor acquisition
failure leaves a failed task and no owned child/adapters; long-lived family
churn retains bounded heap storage without changing selected IDs or custody;
and the named counterfactual mutations now fail the intended assertions.

## Rollout and Rollback

No queue format, TaskSpec schema, persisted index or migration changes are
planned. Land explicit file lists after review. Specs promote before code;
runtime repair, heap repair and test repair remain separate reviewable slices.
Restart Managers through their existing lifecycle when deploying scheduler code;
its pending indexes rebuild through existing recovery. Do not mutate or purge
reserved queues to deploy or roll back.

Rollback is a reviewed revert of the affected code/tests and corresponding
normative delta/implementation mapping. Record that reverting a repair restores
its known defect. Keep the independent test repairs when rolling back runtime
or heap mechanics unless their assertions specifically depend on the reverted
contract. A shared-supervisor rollback must include caller ownership changes
so Host and Docker do not disagree about cleanup. No one-way data conversion is
introduced; process teardown itself is irreversible and must remain scoped to
owned runtime identity, never a broad process-name kill.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|

## Review and Execution Record

Author fresh-eyes review: completed as a separate pass on 2026-10-27. Corrections:
the promotion task now names two spec files, not three; the scheduler call is
`launched`, not a nonexistent `register_launched`; the companion dispatch plan
is described by its revised serial-first scope; and reclamation explicitly
deduplicates live keys after same-ID removal/re-addition. The pass also checked
the existing monitor error distinction, retained text-decoder/CRLF tests,
`communicate()` tradeoff, and local-versus-global heap traversal. The pipe
portability/shutdown and compaction-latency gates remain explicit uncertainties,
not claimed solved by this planning pass. Existing file references and named
regression targets were checked directly; historical plan paths resolve through
the recorded Git revision.

Parent fresh-eyes correction: replaced a combined multi-root pytest command with
the existing separate core and extension/integration CI recipes, and named the
normal suppression-index/formatter gates. This changes no repository test policy.

Plan authoring and independent Tier-2 plan/spec review: complete, with accepted
findings corrected and focused re-review PASS. The parent task used
`skills/call-agent/SKILL.md` and the review-loop runbook; failed Tier-1 attempts
and the permitted same-family fallback are recorded below. This is not
cross-family approval. The primitive design gate and spec promotion still
precede production implementation; review does not claim those experiments
have succeeded.

Implementation, spec-promotion identifier, RED/GREEN results, platform results,
compaction measurements and final review: not performed under this planning
task. Populate them during authorized execution; do not treat the baseline
suite or review probes as completed implementation gates.

### Current reviewer availability and fallback record

2026-10-27: author family OpenAI. Tier-1 Anthropic Claude Code 2.1.293 passed the 120-second liveness probe (`PROBE-OK`) and a write-attempt containment probe (no write tool, probe file absent). Review invocation: `claude -p <embedded-plan-brief> --permission-mode plan --restricted --strict-mcp-config --tools Read,Grep,Glob --allowedTools Read,Grep,Glob --no-session-persistence --effort high`, closed stdin, Python subprocess timeout 540 seconds. Both the admission and repair plan calls reached 540 seconds with empty stdout/stderr and no verdict; neither counts as review approval. Exact briefs, hashes and result records: `/tmp/weft-plan-review-20261027/admission-round1.*` and `repairs-round1.*`.

Second Tier-1 family attempt: Grok 1.0.62, `grok --cwd /Users/van/Developer/weft --sandbox read-only --always-approve --disable-web-search --output-format json --prompt-file /tmp/weft-plan-review-20261027/grok-live.prompt`, closed stdin, 120-second bound. It exited 1 before model startup: “sandbox could not be applied: socket deny resolution failed: could not resolve runtime-socket deny path /var/run/docker.sock: endpoint is a symlink to a path outside the deny list”. It refused to start without protections. No unenforced retry was made. This availability failure applies to both plan reviews.

Tier-2 review is now selected: **same-family fallback (degraded)** via a separate native OpenAI reviewer for each plan, after the logged Tier-1 failures. This is not cross-family approval. CLI timeout diagnosis continues separately; it does not replace review or authorize changes to tool security settings.

Timeout diagnosis: a subsequent bounded Claude call with identical containment and high effort, plus stream-json/verbose/partial-message/debug telemetry, completed a real specification Read in 8.806 seconds (exit 0; model reported as claude-opus-5-5). This rules out a persistent startup/authentication/Read-tool failure at diagnosis time, but does not identify the earlier full-review timeout cause. Those failures remain unclassified timeouts, not proven model stalls or version regressions. Proposed invocation maintenance: retain the security posture and bounded deadline, stream progress and require a final successful result with the requested verdict; progress alone is not approval. No skill or security-setting change was made. Diagnostic artifacts: `/tmp/weft-plan-review-20261027/claude-tool-diagnostic.*`.

### Tier-2 review, round 1 and dispositions

Reviewer: native OpenAI agent `/root/repair_plan_review`, separate from the repair-plan author; **same-family fallback (degraded)**. Unit: this repair plan at source baseline `e220748f`. Verdict: **BLOCKED: F1, F2**. Findings below are the reviewer's failure assertions; no implementation is claimed.

F1 [P2], verbatim:

> Reusing `_drain_streams_until_closed` does not establish bounded drainage. Its deadline surrounds `_drain_stream_queue`, whose inner loop continues until its queue becomes empty. The main supervisor uses the same unbounded helper before cancellation, process-exit and limit checks. A control calling the actual helper with a 10 ms budget and a bounded callback replenishing its real queue remained active after 200 ms; it returned at 230 ms only after replenishment stopped. This is a helper-level counterexample, not a claimed full-process reproduction. The planned silent descendant case misses sustained output.

F2 [P2], verbatim:

> Global pending-ID membership plus deduplication is insufficient to identify a bucket’s live nodes after same-ID re-addition. Actual reducer control: recover root `100`; add `200/201/202` with ancestry `(100,150)` and `999` under `100`; remove `200/201`; recover `150`; re-add `200/201`; remove `202`. Bucket `100` then has `L=1,H=4`. The proposed predicate retains `{200,201,999}`, producing `H'=3 > 2L=2`; `200/201` now belong to bucket `150`. Late recovery exists in Manager’s `_adopt_scheduler_evidence`, and same-ID re-addition is expressly included in the plan’s acceptance. This is an in-memory production-reducer control, not a reproduced complete Manager incident.

| Finding | Disposition | Operative correction and proof |
|---|---|---|
| F1 | Accept within R1/R2 | Task 2 and proposed RM-5.2 insertion require finite per-stream work, deadline checks inside final drainage, and bounded drains in the main supervision loop. Add replenishing-real-queue and sustained-output process controls; preserve stream order/EOF and state explicitly that arbitrary blocking callbacks cannot be preempted. |
| F2 | Accept within S1 | Task 3 checks current `_request_indexes[id].owner()` identity as well as pending membership for both reclamation and candidate pruning, with local deduplication. Add the exact late-recovery sequence and contraction controls. Account separately for owner-handle resolution/path compression; do not promise strict total O(H) without that accounting. |
| CRLF fixture caution | Accept | Adapt the fake chunked-stream test to prove decoding/newline/EOF behavior at a decoder or real-pipe boundary. Do not require production adapters to accept a fake lacking `fileno` or preserve artificial OS read chunk splits. |

Focused re-review, 2026-10-27, same reviewer and degraded posture: **PASS**. Verdict, verbatim: “Focused re-review closes F1 and F2.” The reviewer verified finite inner work/deadlines, fair main-loop drains and sustained-output controls, the callback limit and CRLF fixture boundary, current bucket ownership in rebuild/pruning, the late-recovery regression and owner-resolution accounting. Closing assessment, verbatim: “No new contradiction or robustness regression found in these corrections. A zero-context expert can implement the revised plan subject to its existing pipe-portability and compaction-latency gates. This approves the plan’s instructions, not unperformed implementation or platform results.” No standing constraint or refused alternative was proposed for reversal.

Plan-authoring verification, 2026-10-27: metadata/spec-hygiene tests passed (6 tests, 0.98 s); `bin/check-doc-paths`, `bin/coalesce-check` and `git diff --check` passed. Coalescing evidence: 13 SHA claims (6 foreign), 208 retrieval cues (206 retired-plan cues), all local cues resolve. Plan paths/symbols were also inspected directly because the doc-path checker excludes plans. Only plan/index text and related-plan backlinks changed; no runtime/test implementation or normative spec promotion occurred.

## Implementation Execution Record (2026-10-27)

User authorized implementation of this repair plan followed by admission measurements, with concurrency deferred and review gates retained. Class 5; existing dirty plan/index/backlink edits preserved. Governing theory/context and the reviewed plan consulted. Outcomes: repair R1–R3 ownership, S1 index retention, T1–T4 test sensitivity; verify real regressions, review each meaningful slice, then measure serial dispatch. Queue custody, state transitions, decoder behavior, runtime cleanup hooks, family order and measured admission remain invariant.

Comprehension answers before production edits: (1) descendants retain independent pipe writers after root exit; bound drainage, preserve the observed root outcome. (2) a buffered writer can hold the lock needed by cross-thread close; the cancellable adapter owns close and the supervisor joins it. (3) failed monitor acquisition still leaves the runner owning its already-spawned child, so rollback precedes propagation. (4) leftist rank bounds the right spine only; arbitrary ancestor repair has no promised logarithmic bound, hence local geometric reclamation. (5) forbidden pairs exercise the validator; literal edge expectations prove action/ID/target, while TaskSpec owns idempotence/terminal self-transition rules. All agree with the expected answers.

Task 1 spec promotion: applied the three reviewed insertions to the two governing files, preserving existing clauses and backlinks. Promotion baseline: uncommitted diff against `e220748f786ea72dad00fbc7672fc484e24bef0c`, captured in `/tmp/weft-implementation-20261027/repair-spec-promotion.diff`, SHA-256 `6f17fc6d2a814ebc6ee946c9995aa7aa38fccf3e63e058f9a3ba23b2356f42c1`. These active spec insertions now govern the repair; implementation mappings will be updated with verified code. Pipe primitive research is in progress. Windows execution availability is being clarified; no local macOS result will be called Windows proof.

Test execution is serialized against shared-tree edits; independent read-only research may overlap. Each implementation lane owns disjoint files and requests a quiescent test window. The owner subsequently authorized implementation with monitored Windows CI; commits and a pull request provide that gate. Deployment and merging are outside this task.

Owner-directed platform sequencing: the owner answered “Implement locally. You will monitor CI which has windows runners”. The operative primitive gate now allows local implementation after local probes plus primary Windows/Python source verification, retaining Windows CI as required acceptance. Branch: `codex/runtime-repairs-dispatch-measurements`; CI runs through the existing pull-request workflow. No runtime pass is inferred from source inspection.

Execution sequencing refinement: disjoint heap and test-repair preparation/RED edits proceed alongside runtime primitive research; they have no code dependency on command I/O. Shared-tree test windows remain exclusive of edits, each coherent slice receives review, and integrated repair acceptance precedes admission measurements. This changes work scheduling, not product behavior or review floors.

### Initial failing controls and test-repair implementation

S1 RED: four production-reducer regressions failed at the intended assertion (1.15 s): continuing sibling churn retained 3 nodes at L=1; late recovery retained `[999,202,201,200]` instead of `[999]`; candidate pruning returned cross-owned 201 instead of 200; same-ID re-addition retained duplicate nodes. Parent inspected `/tmp/weft-implementation-20261027/heap-red.log` before authorizing the implementation.

R1–R3/F1 RED: seven core cases failed against unchanged runtime code (7.63 s): timeout/cancel blocked in input, silent and continuous inherited-pipe writers prevented successful/nonzero root return, failed monitor acquisition left root alive, and a replenishing real queue prevented the inner drain from yielding. Two initial Consumer cases had an unavailable `unique_tid` fixture and were rejected as proof. After correcting setup and using atomic PID publication, both reserved KEEP/CLEAR cases failed at “failed Consumer left its owned command alive” (1.12 s), after proving failed state/event, finalization and reserved disposition. Parent inspected both logs. Sources: `/tmp/weft-implementation-20261027/runtime-red.log` and `runtime-consumer-red.log`.

T1–T4 implementation: retained independent literal state-pair oracle now invokes the real validator; its retained 14-edge table checks literal action/ID/target, enabling removal of only the duplicate core allowed-edge table. TaskSpec tests cover three forbidden created edges, nonterminal idempotence and all five terminal self-transitions. Executable preflight observes actual resolver output with configured and default fixture executables, without launch. Three command deadline tests replace the module-local time binding and restore it before cleanup.

Sensitivity evidence: isolated in-memory production mutations preserve imported function aliases and restore code/table objects in finally; source hashes remained unchanged. Final controls produced 44 intended failures: each of the three created→completed/timeout/killed acceptance bypasses exercised both the matrix and TaskSpec assertion (6 failures), wrong spawning→failed action, each 14 edge removals against both retained keepers, five terminal self-transition bypasses, three nonterminal idempotence breaks, and ignored project settings. Baseline pair/action/provider assertions passed their corresponding bad controls, demonstrating why the changes matter. Real SQLiteRunner transaction ownership stayed held across its 50 ms waiter timeout: the original shared-clock patch stalled past 250 ms, while the repaired exact test body allowed the real OperationalError deadline to fire. Parent verified exact failure transcripts and the owned waiter cleanup. Script: `/tmp/weft-t1-t4-controls-20261027.py`; final evidence: `/tmp/weft-implementation-20261027/test-repairs-final-sensitivity.log`.

T1–T4 focused GREEN: the six-file command in Task 4 acceptance passed 196 tests in 6.76 s at default worker concurrency, after deleting the redundant table (initial pre-deletion run: 197 passed in 6.82 s). Scoped Ruff check/format and diff whitespace passed. Independent changed-code review is next; these checks do not close integrated runtime/platform acceptance.

### T1–T4 cross-family implementation review

Tier 1 restored with streaming telemetry: Claude Code 2.1.293 / claude-opus-5-5, read-only restricted Read/Grep/Glob, 540 s bound, completed in 172.36 s with final `subtype=success`, `is_error=false`. Verdict: **no blocker**. Command shape is the recorded call-agent invocation plus `--output-format stream-json --verbose --include-partial-messages --debug-file`; brief/diff/result at `/tmp/weft-implementation-20261027/test-repairs.*`. No runtime source was changed by the reviewer. It could inspect repository code but could not read the external `/tmp` logs under containment, so execution evidence remains parent-verified at this round. Focused follow-up will embed the transcripts rather than weakening containment.

Findings, verbatim:

> F1 (nit): For `created`, the "before" snapshot is taken after the first `set_status("created")`, and that call is itself a same-status update. A change that rewrites a field the same way on every same-status call would already be in the snapshot, so a `created`-only idempotence break would pass. The `spawning` and `running` iterations do take their snapshot before any same-status call, so a general break is still caught.

> F2 (nit): The record claims "44 intended assertion failures", but the listed controls add up to 41 (3 + 1 + 28 + 5 + 3 + 1). The total only reaches 44 if each forbidden-edge bypass is counted twice, once for the pair matrix and once for the TaskSpec test. That isn't stated.

Dispositions: accept F1 with snapshot-before-first-created-call and a targeted stable-field-rewrite control; accept F2 by explicitly counting the six matrix/TaskSpec forbidden-edge failures above. The reviewer found T2's configured/default executable trap and T3's module-local clocks consistent with the actual code. Windows resolution remains for CI. Out-of-scope observations about older raw-machine checks and broad ValueError assertions are retained unchanged, as requested by the plan.


Focused T1–T4 Tier-1 re-review completed successfully in 46.50 s with the same contained Claude invocation: **PASS**, F1 and F2 closed, no new defect. The 44-failure run above predates F1; its failure locations reflect that snapshot. The F1-specific stable-field-rewrite control survived the old test and failed the repaired whole-state assertion; restored production passed, and the focused file passed 17 tests in 1.19 s. Evidence: `test-repairs-f1-sensitivity.log`, `test-repairs-f1-green.log`, and `test-repairs-round2.result.json` in the execution evidence directory. Reviewer execution claims remain explicitly supplied transcripts, not independent reruns.

### Runtime primitive and local implementation evidence

The owned adapters use raw nonblocking pipes, finite reads/writes, stop-event waits only on would-block, and owner-thread close. They are registered before start and joined on every exit. `io.IncrementalNewlineDecoder` preserves the standard text newline/codec behavior; no custom CR parser remains. The execution clock starts before input delivery; root exit starts bounded final drainage independently of inherited writers. The existing runtime stop/kill hooks and primary exception survive rollback.

Disposable real-pipe probes (`/tmp/weft_raw_pipe_gate_20261027.py` and `.json`) on Python 3.14.9/macOS 27.0.1 arm64 reached EAGAIN after 65,536 bytes of a 1 MiB blocked input; supervision ran 18 turns in 0.207 s and adapter stop/join took about 0.1 ms. A silent inherited writer drained for 0.250877 s; continuous output drained for 0.2511 s with the descendant still alive. Large Unicode input and 25 split UTF-8/newline/error cases matched standard text decoding. These are macOS results only.

Primary portability evidence: [Python 3.12 os.set_blocking](https://docs.python.org/3.12/library/os.html#os.set_blocking) documents Windows pipe support; [CPython 3.12.0 fileutils.c](https://github.com/python/cpython/blob/v3.12.0/Python/fileutils.c) implements PIPE_NOWAIT and would-block translation for reads and writes. [CPython subprocess](https://github.com/python/cpython/blob/3.12/Lib/subprocess.py) retains synchronous Windows input writing and reader threads in communicate, so it does not supply this cancellation boundary. [IncrementalNewlineDecoder](https://docs.python.org/3.12/library/io.html#io.IncrementalNewlineDecoder) supplies the existing standard decoder primitive. Actual Windows behavior remains a CI gate.

Local runtime GREEN: 276 tests passed in 33.60 s across the subprocess runner, TaskRunner, Consumer terminal events and command-runner parity files at default worker concurrency. Docker caller unit tests passed 87 tests in 0.29 s using their normal extension recipe. Isolated controls restored the baseline blocked-input path, removed drain bounds/deadlines, registered adapters after start, masked the primary exception, reset decoder state, skipped final decoder flush, used the wrong input codec, skipped acquisition rollback, and ignored adapter errors. Each failed its intended behavioral assertion; source hashes remained unchanged. Exact transcripts and control manifests are under `/tmp/weft-implementation-20261027/runtime-control-*`, `runtime-controls.json`, and `runtime-extra-controls.json`. Cross-family runtime review is pending; these passes do not establish Windows acceptance.


### S1 retention and Manager latency evidence

The reducer tracks physical/live counts, reclaims only its local heap when H > 2L, and checks both pending membership and current owner-handle identity. Pairwise leftist meld preserves meldability; local deduplication handles same-ID refill. Owner resolution/path compression is counted separately from local traversal. All 44 reducer/Manager/system tests passed in 16.03 s. Four isolated mutants (no reclamation, membership-only rebuilding, membership-only pruning, and retained duplicates) failed the exact intended assertions. Full mypy found a test tuple inference issue; an explicit variable-length tuple annotation corrected it without changing behavior.

Alternating baseline/repaired measurements covered L=100/1,000/10,000, unrelated backlog 0/10,000, three repetitions per cell (36 runs). Largest instrumented compaction times were 0.072/0.640/6.037 ms respectively. At L=10,000, each measured reclamation visited 19,999 local nodes with 22,893 meld calls, independent of unrelated backlog. Retained nodes peaked at 19,999 instead of 40,000 after 30,000 churn cycles. Median total loop time increased from 146.5–147.6 ms to 168.4–169.5 ms: this repairs a memory bound at CPU cost, not a throughput improvement. Scripts/raw samples: `heap-measure.py`, `heap-measure.jsonl`; mutations: `heap-mutations.jsonl` and `heap-mutants/*/result.log` under the execution evidence directory.

The staged real-Manager gate used actual broker classification, exact competing reservations, production stale-hint removal, and normal process_once admission/launch. The observer queued a keyed PING immediately before production compaction. Warmup L=100 compacted H=201 to 100 in 0.107 ms with PONG observed in 3.004 ms; L=10,000 compacted H=20,001 to 10,000 in 5.422 ms with PONG observed in 7.434 ms, in the same turn. Exact source absence, reserved custody and hint deletion were checked before compaction; each case launched root, descendant tip and witness processes, with a completed witness/outbox and all six owned PIDs absent after cleanup. This is staged integration, not a natural 20,000-process workload or a universal latency bound. The first probe mistakenly registered its in-process Manager for harness termination and exited by SIGTERM without a result; it is retained as failed evidence. The corrected probe follows the existing explicit Manager-owner fixture and exited zero. Artifacts: `heap-manager-gate.py`, `.jsonl`, `.stderr`, plus preserved `.attempt 1` files. The observed 5.4 ms pause and 7.4 ms control response support retaining local geometric compaction; larger buckets retain the disclosed linear pause risk.


Real Docker command proof used the production plugin/runner/shared-supervisor call path with the existing `python:3.12-bookworm` image, network disabled and no mounts. A 500,000-character Unicode roundtrip plus independent stderr SHA-256 passed in 0.593 s. A 32 MiB write to a nonreader reached observed EAGAIN and returned timeout in 0.606 s for a 0.3 s execution limit. Cancellation after observed EAGAIN returned in 2.499 s through the existing Docker stop hook. All three calls removed their uniquely owned containers, reaped the actual CLI process and joined adapters. The initial probe misused DockerClient as a context manager and was corrected to `contextlib.closing` before these valid runs; final label inspection found no owned containers. Evidence: `runtime_docker_smoke.py`, `runtime-docker-smoke.log`, `.json`. This validates Docker command cleanup locally, not Windows host semantics or container descendant pipe inheritance.


Integrated local checks: full SQLite/slow suite produced 5,950 passes, 49 skips and two Ruff inventory expectation failures in 196.57 s. Both failures were stale literal registry expectations, not runtime assertions: removed decoder suppression group 037, added adapter-failure group 354, total directives 327→326, raw C901 115→113. Updating those explicit expectations preserved the inventory contract; all 44 Ruff-policy tests then passed in 3.61 s. Full mypy passed all 478 files; Ruff check/format and standalone registry check passed. Six plan/spec hygiene tests passed in 1.94 s, and doc-path, coalescing retrieval and whitespace checks passed. Local extension checks: Django 163 passed/one PostgreSQL-specific skip (55.33 s), Docker 113 passed (0.35 s), macOS sandbox 26 passed (0.11 s), Microsandbox 81 passed/four deselected (1.62 s). Linux-owned extension CI is still required; local macOS checks do not replace it. Full PostgreSQL verification is running with the normal wrapper, capacity and worker configuration.


### Runtime Tier-1 implementation review and disposition

Claude Code 2.1.293 / claude-opus-5-5, same restricted read-only invocation, completed successfully in 341.61 s. Verdict **blocker: F1**; F2 evidence-gated. Full verbatim review follows. The reviewer had embedded test transcripts but could not access external temporary files directly. No independent rerun is claimed.

<details>
<summary>Runtime review findings and verification</summary>

The review is done. I made no edits, and I couldn't write a plan file because this session only has read-only tools.

## Findings

| ID | Sev | Location | Finding | Suggested disposition |
|---|---|---|---|---|
| F1 | **P1** | `weft/core/runners/subprocess_runner.py:328`, `:660`; `weft/_constants.py` `SUBPROCESS_STREAM_DRAIN_MAX_CHUNKS` | **The new chunk cap also limits how fast normal output can be read, so successful commands can lose output.** Each drain pass stops after 16 chunks, but the loops still sleep afterwards even when chunks are still queued. The main loop sleeps for `sleep_for`, up to `ACTIVE_CONTROL_POLL_INTERVAL = 0.05` s, so it reads at most about 320 chunks/s per stream. The final drain sleeps 0.01 s between passes, so in its 0.25 s budget it can read about 400 chunks per stream. The output threads read into unbounded queues with no backpressure, so a root process (the command's own top-level process) that writes quickly reaches its normal exit with a backlog already captured. Example: a child with no descendants writes thousands of small flushed lines and exits. The supervisor breaks at `:206`, the final drain hits its deadline, and the result is `status="ok"` with truncated `stdout`/`value` and a missing `on_chunk("", True)` end-of-stream callback. The old code emptied the queue on every pass, so this case did not lose output. That is a regression from this change, not the accepted risk about descendants writing past the drain boundary. RM-5.2 asks for finite work per stream "before returning to other streams and supervision". It does not ask for a sleep while a backlog remains. No current test covers it: the large-input test produces about 15 chunks of 64 KiB. | Sleep (main loop and `_drain_streams_until_closed`) only when the last pass found both queues empty. When the 16-chunk budget runs out, go straight back to the other stream and the supervision checks. Keep the deadline check inside the drain. Add a real-child test with no descendant: set `SUBPROCESS_STREAM_READ_SIZE` small (as the CRLF test does), emit more than 2,000 chunks, exit 0, and assert the full `stdout` plus exactly one final callback. It fails against the current code. |
| F2 | P2 | `subprocess_runner.py:576-580`, `:437` | **Error handling when the child stops reading stdin is narrower than the standard library's, and that path now raises.** The input thread ignores only `BrokenPipeError`. Errors from the pipe threads are now raised on the success, timeout and cancel return paths (`:437`). The standard library's `Popen._stdin_write`/`communicate` also ignores `OSError(errno.EINVAL)` on Windows ("child exited or closed the pipe", bpo-19612/30418). If CPython ≥3.12 does not translate that case for nonblocking writes, a timeout, cancel, or early exit with unread input becomes an exception on Windows. That would turn an accepted outcome into an error during cleanup, which the plan says should stop work for review. No test on any platform covers the plain case either: a root that exits 0 without reading large input should return `ok`. Removing the `BrokenPipeError` branch would not make any listed test fail. | Add a real test where the child exits 0 immediately without reading 8 MiB of input and assert `ok` with stdout. Either handle Windows `EINVAL` the way `Popen._stdin_write` does, or record Windows CI proof (this test plus the R1 timeout/cancel tests) that it is never raised. |
| F3 | P3 | `subprocess_runner.py:423` | `sys.exception()` in a `finally` block returns the topmost exception being handled on the thread's stack, not only this function's. If a caller calls `run_monitored_subprocess` from inside an `except` block, a normal return looks like a failure. Pipe-thread errors are then dropped (incomplete I/O reported as success), and monitor-stop failures are suppressed. No current call site does this, so the risk is latent. | Record the primary exception in the existing `except BaseException as exc:` clause and test that local instead. |
| F4 | nit | `subprocess_runner.py:261-262`, `:158/:172` | `if process_returncode is not None` cannot be true there any more, because the loop now breaks at `:206`. `_raise_pipe_errors` also runs twice per pass. | Remove the dead branch and one of the two calls. |

**Verdict: blocker: F1.** F2 is evidence-gated: close it with the test, then either the `EINVAL` handling or Windows CI proof.

## What checks out
- **R1 (input blocks supervision):** the timeout clock starts at function entry. The input thread does nonblocking writes and stops on the stop event. The cancel RED control fails as intended. By source reasoning, the timeout variant also blocks in the old code's `stdin.write`.
- **R2 (inherited pipes keep the run open):** completion is accepted on the root's exit alone. The drain is bounded and logs a diagnostic. The completed root's status is not changed to timeout, because the test's 0.25 s drain is longer than its 0.2 s timeout. The descendant is confirmed still alive after return.
- **R3 (monitor failure leaks the child):** monitor load, validation and pipe setup are all inside one rollback that uses the existing stop/kill hooks. The original exception object is preserved. The Consumer test validates the monitor after the real process is created and checks the reserved queue under both policies.
- **Cleanup order:** every pipe thread is told to stop before any is joined. The supervisor never closes a stream that a blocked thread is still using. A failed thread start is still joined.
- **Docker:** container stop/kill still runs through the hooks, and `_cleanup_process` is unaffected.
- **Text handling:** `IncrementalNewlineDecoder` with `translate=True` matches how `Popen` decodes text output. The input side's `"\n"→os.linesep` matches `Popen`'s default `newline=None`. The decoder-state and final-flush RED controls fail correctly.
- **Test fakes:** the tests' `os`/`threading` stand-ins pass calls through to the real functions and only observe or inject the documented failures. Nothing fakes `Popen`, process liveness, the supervision loop, or the broker.

## Missing evidence
1. **Windows CI, the required acceptance gate, is not in the supplied evidence.** The plan's record says the probes are "macOS results only." Watch these in particular:
   - The large-input test. Writes are 64 KiB slices, while Windows' default anonymous pipe buffer is about 4 KiB. If a nonblocking write that cannot fully fit is all-or-nothing (CPython's `fileutils.c` maps `ENOSPC` to `EAGAIN` for nonblocking pipes), the input thread would spin forever.
   - The R1 timeout/cancel tests (see F2).
   - The CRLF/newline translation tests.
2. I could not read `/tmp/weft-implementation-20261027/*`: the file tools are restricted to the repository. Everything I checked from the run logs comes from the pasted transcripts.
3. No RED control was shown for the R1 `timeout` parameter or for `test_continuous_output_yields_to_supervision`. The latter probably would not fail with only the 16-chunk cap reverted, since real pipe throughput rarely outpaces the drain. The queue-level replenishing test covers the cap instead, which is acceptable.
4. No ruff/mypy transcript was supplied for this unit. The suppression registry does include the new `RUFF-SUP-354` entry.

## Observations (not actionable this pass)
- Each pipe thread wakes every `SUBPROCESS_POLL_INTERVAL_FLOOR` (10 ms) while idle, about 200 wakeups/s per running command. The old blocking reads woke only on data. The plan prescribed this design, but it matters for long-running commands at high concurrency.
- Even after F1 is fixed, the 0.25 s final budget includes callback time for output already captured. Slow per-chunk callbacks such as the Consumer's `_publish_consumer_worker_event` can still truncate a root's own late output. This is inside the accepted "output beyond the bounded final drain" risk, so I'm only noting it.
- The timeout clock now also counts `on_worker_started` callbacks and `monitor.start()`, which may include Docker API calls. RM-5.2 allows this ("no later than input delivery").
- `cleanup_phase` stays `"completion cleanup"` when a stream callback raises mid-run, so the monitor-stop log can mislabel the phase.

</details>

Disposition pending reproduction: F1 is in scope because finite per-stream work must yield to supervision, not force idle sleep while known output remains. F2 needs a real early-exit input test and platform proof. F3 can replace ambient exception state with locally captured ownership; reproduce before changing it. F4 removes dead work only after checking behavior. No idle-adapter architecture expansion is authorized by the non-actionable observations. Shared source remains frozen until the current PostgreSQL suite exits; disposable probes may run against the frozen owner.


### S1 Tier-1 implementation review and disposition

Same contained Claude invocation completed successfully in 308.84 s: **no blocker**. The review brief predates the Manager gate and current execution-record/mapping updates; a focused follow-up will include those actual artifacts. Full verbatim review:

<details>
<summary>Heap review findings and verification</summary>

## Findings

| ID | Sev | Location | Finding | Suggested disposition |
|---|---|---|---|---|
| F1 | P3 | `heap-measure.jsonl`, all rows | `owner_edges` is 0 in every row. The benchmark puts all entries straight into bucket 100 with no contracted chain, so the "worst compaction" figures cover heap visits and melds but not owner-handle resolution. The plan requires that resolution to be accounted separately. Depth 1/8/64 is covered only by the reducer test, which has five entries. | Either add one row with live entries behind a retired chain (for example L=10,000, depth 64), or record in the execution record that owner resolution at scale is covered only by the depth test. Don't rerun the whole matrix. |
| F2 | P3 | `tests/core/test_family_scheduling.py:358` (meld test), `:403` (locality/compression test) | These two tests appear only in GREEN. The RED run had 4 tests, and each of the 4 mutants targets one of those 4. Run against the baseline, these two fail on `AttributeError` or signature errors, which proves nothing. From reading the code I'd expect them to catch: merge dropping the `physical_count` transfer (`==7`), `_owns` checking `indexes` before `pending` (the index-probe set would gain 300–302), and compaction iterating a global map (`_LookupOnlyDict` raises). None of those kills is recorded. | Run those three in-memory mutants and record which assertion fires. No new tests needed. |
| F3 | P3 | `heap-green.log` | The log has no command line, no source/test hash and no lint/type result. 44 does match the planned three-file family gate (24 + 18 + 2, counted from the source). The test adds a `dict.get` override against typeshed's overloaded signature and `NoReturn` overrides, and mypy hasn't been shown on them. | Record the exact command and the source SHA (`1f7b1e8a…`, as in the mutation manifest). Run scoped `ruff check`, `ruff format --check` and `mypy` on both files. |
| F4 | nit | `weft/core/family_scheduling.py:133` | `_owns(...)` is evaluated before `request_id not in seen`, so a duplicate same-ID node resolves its owner a second time. | Swap the order: `request_id not in seen and self._owns(...)`. The test comment at `:469` should then say "at most once". |
| F5 | nit | `tests/core/test_family_scheduling.py:472` | `set(index_view.probes) == {303, 999}` would not notice a repeated resolution of 303 (0 steps, so `sum(owner_steps)` doesn't catch it either). | Use `sorted(index_view.probes) == [303, 999]`. |

## Verdict

**No blocker** in this unit's code or tests. The slice still can't be closed: the Manager control/reap latency design gate is pending, and I have not inferred a pass for it.

## What I checked

**The node-count bound H ≤ 2L holds (H = stored nodes, L = live entries).**
- `add` adds one node and one live entry.
- `merge` sums both counts and zeroes the source. Two bounded buckets stay bounded.
- Removing the root and pruning stale roots in `candidate` each subtract exactly one node.
- An emptied bucket resets to H = 0.
- After compaction H equals L. Every live ID always has a node in its owner's heap, since nodes only arrive via `add` and move via `merge`, and pruning drops only nodes not owned by the bucket.
- Compaction can only start in `remove`, starting from H ≤ 2(L+1). So it fires with at least L+1 stale nodes.
- Every stale node is discarded exactly once, even across merges. Rebuild work is therefore bounded by the number of removals, plus owner resolution. Owner resolution uses path compression without union-by-rank, which is amortized logarithmic, not constant. The docstring says this correctly.

**The rebuild is sound.**
- Bottom-up pairwise meld of singletons is O(n) in total.
- Reusing nodes is safe because each node belongs to exactly one heap. Discarded nodes become unreachable.
- Keys don't change and only same-key duplicates are dropped, so selection order is preserved.

**Ownership and locality.**
- `remove_pending` (`:285–289`) drops the request from both pending maps before calling `index.remove`, so the removed ID never counts as owned.
- Compaction runs only on an owner bucket.
- Nothing outside the module calls `_PendingIndex` or `candidate`.
- Setting `other.count = 0` in `merge` changes behaviour from the baseline. I confirmed nothing reads a merged index's count: every path calls `.owner()` first, and `retire` pops the source from `_parents`.

**Tests.**
- I traced all five new tests by hand against the current source. Their asserted states (L, H, retained IDs, selections, step sums) match.
- The late-recovery test runs the exact sequence the plan requires.
- The probe-count equality in the locality test is exact: only the third removal triggers compaction.
- The RED and mutation transcripts fail at the intended assertions. The mutants that keep only the global-membership check fail the rebuild test and the candidate test respectively.

## Evidence I couldn't confirm
- **`/tmp` logs:** the read-only tools here can't open them, so I relied on the transcripts you pasted.
- **Mutant test copies:** each mutant ran a copy of the test file (`test_heap_snapshot.py`). No hash ties those copies to the repository test file, and each mutant ran only one test.
- **Execution record:** the plan has S1 RED only. There are no S1 GREEN, measurement or mutation entries yet.
- **[MA-1.8] implementation note:** Task 3 lists it as a file to change. It isn't in this unit, so I didn't review it.

## Observations (not actionable this pass)
- **Candidate ownership check may never fire through `select()`.** With consistent ancestry I couldn't find a way to reach a cross-owned stale root via `select()`. A bucket is only consulted when its TID is a frontier tip, and path normalization means no resident descendant exists at that point. The only window I see is between a deferred `recover` and `finish_recovery`. The plan requires the check and it costs about one amortized owner lookup per select, so keep it. The direct test of the private primitive is justified for the same reason.
- **Where the pause lands.** `manager.py:5780` calls `remove_pending` after the broker connection block has exited. A compaction pause therefore doesn't hold a broker connection, but it does land on the reactor turn. That is what the pending latency gate has to judge.
- **Timings are upper bounds.** The benchmark wraps `_owns` and `owner` in the repaired arm, so the compaction times (about 5.7 ms at L=10,000) and the roughly 15% loop overhead are inflated by instrumentation.
- **Retained memory.** The baseline keeps 4L nodes after 3L cycles and grows linearly. The repaired version peaks at 2L−1, independent of unrelated backlog. That supports the retention fix but says nothing about the Manager latency gate.

</details>

Dispositions: accept F1 with one additional large bucket/deep owner-chain measurement; accept F2 with three isolated mutation controls for the two newer tests; accept F3 with exact command/hashes and static-check evidence. Accept F4/F5 as removal of duplicate owner work plus an exact probe-count assertion, after the current shared-tree test window closes. No broad benchmark rerun or scheduling change is required. The measured heap times are instrumented observations, not runtime guarantees.


Owner review requirement (2026-10-27): before completion, perform both a fresh-eyes implementation review and a separate other-model/cross-family review on the final combined delta. Both must explicitly answer: Is it correct? Is it elegant enough for an expert systems programmer? Does it use the underlying data structures properly or overcomplicate them? Verify findings against actual code, firing tests, theory and measurement evidence; retain dispositions and focused re-review of accepted fixes. Earlier slice reviews do not replace these final passes.


S1 review follow-up evidence (F1–F3): one additional L=10,000/depth-64 contracted-owner measurement rebuilt H=20,001 to 10,000 in 7.106 ms with 20,001 heap visits, 10,000 live-owner resolutions, 10,063 traversed owner edges (64+9,999 after path compression), and 22,894 meld calls. This is instrumented and supplements, rather than replaces, the original matrix. All three requested mutants fired: omitted physical-count transfer yielded 4 instead of 7; looking up owner before pending membership added stale IDs 300/301/302 to probes; scanning the global pending map raised the lookup-only map assertion. The latter two failed all six depth/population cases. `heap-evidence-manifest.json` ties seven snapshots to current tests: the new three normalize byte-identically after import-path substitution; the earlier four differ only by the recorded explicit tuple type annotation. Exact commands, hashes, normalized diffs and static results are preserved. The completed 44-test command was `./.venv/bin/python -m pytest -m '' tests/core/test_family_scheduling.py tests/core/test_manager_family_maintenance.py tests/system/test_family_scheduling.py`; full mypy passed 478 files. F4/F5 edits remain pending the frozen PostgreSQL run.


### Resumed implementation, 2026-10-28

The native runtime implementation agent hit the account usage limit before saving the new review-regression tests. This was not a Tier-1 reviewer failure: both Claude slice reviews completed and their findings remain above. The owner restored usage and authorized continuation. Same branch and dirty tree retained; no commit/push has occurred. Claude Code is still 2.1.293, so the same session's verified read-only invocation remains applicable.

Full PostgreSQL acceptance of the pre-review-correction snapshot passed 5,903 tests with 27 skips in 345.38 s through `./.venv/bin/python bin/pytest-pg --all`, with default worker configuration and canonical server capacity. The repository environment was restored with `uv sync --all-extras --frozen`. This run predates the accepted review corrections and does not certify them.

Runtime disposition evidence: F1 reproduced on the real runner with a 32-byte read size: a root wrote 100,000 bytes and exited zero, but the runner returned ok with 12,288 characters and no EOF callback. An isolated no-idle-sleep-while-queued variant returned all 100,000 characters and one EOF callback. F3 reproduced by calling the real runner inside an unrelated caller exception handler: fatal monitor cleanup was suppressed only in that case; a function-local failure flag preserved the correct throwable. Accept both corrections and new regression tests. F2's real early-exit/8 MiB input probe passed on macOS, observing actual BrokenPipe; add a keeper and narrow Windows EINVAL handling matching CPython's documented closed-input case. The platform partition is an injected error test, not Windows syscall proof. [CPython 3.12.0 subprocess.py](https://raw.githubusercontent.com/python/cpython/v3.12.0/Lib/subprocess.py) ignores EINVAL for child exit/closed stdin; [fileutils.c](https://raw.githubusercontent.com/python/cpython/v3.12.0/Python/fileutils.c) halves Windows ENOSPC writes until partial progress or EAGAIN, so the review's 64 KiB all-or-nothing concern does not match that primitive. Windows CI remains required. F4's dead-branch claim is declined: the timeout path's second poll can observe newly exited root after the first poll returned None. Preserve that behavior and the error checks before/after callback drains. Exact probe script/results: `runtime_review_repro.py`, `runtime-review-repro.json`.

S1 F4/F5 edits now remove duplicate owner resolution before the ownership check and assert exact probe cardinality; focused verification is pending. The 169 versus 147 ms reducer totals above include asymmetric observer costs and must not be read as a 15% production slowdown. The depth-64 measurement also ran during PostgreSQL load. The real Manager probe has only the outer compaction timer and is recorded separately.


Runtime review corrections RED: four intended failures and two passes in 4.90 s at 12 configured workers. The exited-root output case lost stdout, the running-root case timed out instead of returning ok, the ambient-exception cleanup case failed to raise its original fatal signal, and injected Windows EINVAL propagated. Real pre-exited-root/8 MiB BrokenPipe and non-Windows EINVAL propagation passed. Parent inspected exact assertions in `runtime-review-red.log`.

Corrections keep the finite batch/deadline checks but sleep only when both output queues are empty, replace ambient exception lookup with function-local failure tracking, and accept EINVAL only on Windows writes. The four-file runtime gate then passed 282 tests in 34.11 s; Docker caller tests passed 87 in 0.32 s. Isolated reverts of the main-loop sleep guard and final-drain sleep guard failed the running-root and full-output assertions respectively; removing BrokenPipe handling raised the actual closed-pipe error; broadening EINVAL suppression to all platforms failed the Linux negative test. The real Docker smoke passed again (roundtrip 0.597 s, timeout 0.558 s, cancel 2.306 s), with all owned containers, CLI processes and adapters released. Records: `runtime-review-green.log`, `runtime-review-docker-green.log`, `runtime-review-controls.json`, and the updated Docker smoke files. The original smoke timing record remains above as historical evidence. Static follow-up found two new-test format wraps and an unsupported sorted(list[object]) heap assertion; the latter is corrected with exact cardinality plus set membership, preserving duplicate sensitivity without casts. Focused re-review and final integrated verification remain pending.


Review invocation hygiene: the first runtime round-two dispatch was stopped before accepting a verdict because its brief omitted explicit standing-constraint and pre-existing-concern clauses. The corrected brief now includes all seven required scope elements; the incomplete invocation is retained as `runtime-round2-incomplete-brief.*`, not counted as reviewer unavailability. The corrected contained invocation is running.


S1 retained raw timing samples (milliseconds, repetitions 0/1/2 in recorded alternating order; instrumentation described above):

| Live entries | Unrelated entries | Baseline loop ms | Repaired loop ms | Repaired largest compaction per repetition ms |
|---:|---:|---|---|---|
| 100 | 0 | 1.094, 1.089, 1.112 | 1.330, 1.276, 1.320 | 0.067, 0.060, 0.054 |
| 100 | 10000 | 1.055, 1.123, 1.114 | 1.396, 1.317, 1.400 | 0.072, 0.061, 0.065 |
| 1000 | 0 | 12.947, 12.839, 12.787 | 14.304, 14.518, 14.899 | 0.640, 0.583, 0.604 |
| 1000 | 10000 | 12.895, 12.840, 12.799 | 14.847, 14.569, 14.399 | 0.547, 0.559, 0.557 |
| 10000 | 0 | 145.632, 146.510, 149.045 | 168.429, 168.645, 164.205 | 5.694, 6.037, 5.629 |
| 10000 | 10000 | 145.780, 147.573, 148.629 | 167.736, 170.045, 169.485 | 5.885, 5.664, 5.766 |

Corrected full SQLite/slow verification now passes **5,958 tests**, with 49 expected platform/backend/live-provider skips, in 202.89 s. Full mypy passes all 478 files, and Ruff check/format plus registry consistency pass. Final PostgreSQL verification is running. S1 final focused gate passes 24 tests in 1.97 s; all seven final isolated controls fail at the intended assertions (17 parameter cases total). Reverse-normalized control test copies match the final test source exactly.


### Runtime focused Tier-1 re-review

Corrected contained Claude invocation completed successfully in 238.62 s: **no blocker**, F1–F3 correct. It answered the three owner questions positively for this correction slice, without claiming Windows execution or final combined review. Verbatim findings:

<details>
<summary>Runtime round-two findings and verification</summary>

I didn't edit, delegate or run anything. This session only has read-only tools, so I couldn't write the plan file or call ExitPlanMode. The review is below.

## Findings

| ID | Sev | Location | Finding | Suggested disposition |
|---|---|---|---|---|
| R2-1 | P3 | `tests/core/test_subprocess_runner.py:1529-1573` (tests the check at `subprocess_runner.py:589`) | **The new test only covers half of the Windows check.** The production check needs two things: the platform is `win32` **and** the error is `EINVAL`. The test's two cases are win32 with `EINVAL` (accepted) and linux with `EINVAL` (raised). A mutant that drops the error-code half (`if sys.platform == "win32": return`) passes both. That mutant would silently treat any Windows input I/O error, such as `EIO`, as a clean close and could report incomplete input as `ok`. The docstring says the test separates the closed-pipe error from "real I/O faults", but no real fault is tested on the accepting platform. | Add a third case, win32 with `OSError(errno.EIO)`, and require `caught.value is failure`. That only extends the parameter list; no new test is needed. |
| R2-2 | nit | `subprocess_runner.py:330-331` | **The comment gives the wrong reason.** It says "The adapters are sole producers." The no-spin argument actually rests on the supervisor being the **only consumer**. Because nothing else takes from the queue, a non-empty result from `empty()` guarantees the next pass makes progress. A stale "empty" result only costs one normal poll interval. The brief states it that way; the code comment doesn't. | Change it to say the loop is the sole consumer, so "non-empty" guarantees progress and a racing put only delays one poll. |
| R2-3 | nit | `test_subprocess_runner.py:1374`, `:1529` | **Two test IDs are slightly misleading.** In `"exited"` the root isn't already gone at the start: it blocks on pipe capacity and exits during the run, without waiting for the test's release file. `"linux"` runs on darwin; it really means "any non-Windows platform". | Optional rename, e.g. `exits-unprompted`/`awaits-release` and `posix`. |

## Verdict

**No blocker.** F1, F2 and F3 are correct as implemented. R2-1 is a small gap in what the tests can detect, not a production flaw.

## Your three questions

**1. Is the implementation correct?** Yes.

- **F1 (no idle wait while output is queued):**
  - Since the supervisor is the only consumer, a non-empty `empty()` result means the next drain will take at least one item. The loop never spins without making progress.
  - In the main loop, every pass still runs the pipe-error checks (`:160`, `:174`), cancellation (`:175`), `poll` (`:207`), timeout (`:211-215`) and the time-gated monitor check (`:272-276`). So a continuously writing root cannot delay supervision.
  - In the final drain, the deadline is still checked inside `_drain_stream_queue` (`:621`) and in the loop condition (`:654`). Once the deadline passes, both calls return without dequeuing and the loop exits, so the absolute budget holds.
  - `cancel_requested` is in-memory flags only (`consumer.py:486-491`, no broker access) and monitor sampling is time-gated. Skipping the wait therefore adds no extra I/O or samples.
  - The reviewed timeout path, where the second poll sees the root exit, still works. The root is reaped on the next pass whether or not the wait is skipped.
- **F2 (Windows `EINVAL`):** Handler order is correct: `BlockingIOError`, then `BrokenPipeError`, then the general `OSError`. `sys` is used only at `:589` now, so the test's `SimpleNamespace(platform=...)` replacement is safe. In the linux case, the adapter records the error before it closes stdin, and the child can only exit after that close. So either the in-loop check or the `finally` check (`:442`) raises the same exception object.
- **F3 (local failure flag):** The flag is set as the first statement of the only `except BaseException`, which covers every exception from this call. A `try/else` wouldn't work because the body returns from several branches, so a flag is the right tool.
  - The fix also restores a second guarantee the old `sys.exception()` lost inside a caller's `except`: the success-path `_raise_pipe_errors` (`:442`) was skipped entirely there. Both behaviours hang off the same flag, so the new test is enough.
  - The existing primary-failure test (`:1335`) catches the opposite mutant, where the flag is never set.

**2. Is it elegant?** Yes.
- F1 is a two-line guard, identical at both sites, and leaves the 16-chunk-per-pass limit and its fairness alone.
- F2 is narrower than CPython's own `_stdin_write`, which accepts `EINVAL` on every platform.
- F3 replaces process-wide exception state with one local boolean.
- Nothing new was added: no constants, helpers or changed return types.

**3. Does it use the data structures properly?** Yes.
- `Queue.empty()` is used only as a hint by the single consumer, and nothing is dequeued or dropped based on it.
- The Event/nonblocking-pipe design and the 16-chunk-per-pass limit are untouched.
- Taking the 16 + 16 items as single `get_nowait` calls, without sleeping between batches, is the minimal change. Sleeping only when there is no work is the right semantics for the already-reviewed polling design.

## Test sensitivity checked against the controls

- **Output tests:**
  - Each restored wait is caught by one parameter. Main-loop wait: `running` returns `timeout`. Final-drain wait: `exited` truncates. The arithmetic matches the evidence: about 3,125 chunks per stream; 16 per 50 ms is about 9.8 s against the 3 s timeout; 16 per 10 ms in 0.25 s is about 400 chunks, roughly the 12,288 characters observed.
  - The exact-one-final-callback assertion catches both a missing and a duplicated end-of-stream marker.
- **Early-exit input test:** removing the `BrokenPipeError` branch fires on a real closed pipe. The callback waits on the write attempt, so the test can't pass before the closed pipe has actually been written to.
- **Windows `EINVAL` test:** broadening acceptance to all platforms fails the linux case. A `continue`/retry mutant would hang until the 5 s timeout and fail the win32 case. The only surviving mutant is the error-code one in R2-1.
- **Caller-exception test:** fails against the round-1 code (the RED log shows `DID NOT RAISE`).

## Non-actionable observations

- **Timing margin of the `exited` case.** It needs about 6,250 trivial callbacks to finish inside the private 0.25 s drain after the root exits. The supplied repro took 0.055 s in total, so the margin is several times over. If it ever fails under the 12-worker gate, per [ALT-WT-017] that's a signal to investigate, not a reason to reduce workers.
- **Unbounded output queues** were already in the design. This change makes them grow less, because the consumer no longer throttles itself.
- **Narrow signal window in F3.** An asynchronous `KeyboardInterrupt` arriving between entering `except` and the flag assignment could still be replaced by a cleanup error. The old code had the same window, and nothing practical closes it.
- **Windows execution is still unproven.** The win32 case uses an injected error; it is not a real Windows syscall. Windows CI remains the gate, as the owner has authorized.

</details>

Dispositions: accept R2-1 by adding Windows EIO to the explicit input/error acceptance matrix and proving the platform-only mutant fails. Accept R2-2 by correcting the single-consumer rationale in the code comment. Accept optional R2-3 with precise scenario IDs, preserving historical evidence under its original IDs. No production behavior change is needed. These edits wait for the active PostgreSQL verification to finish.


### S1 focused Tier-1 closure

Contained Claude re-review completed successfully in 127.76 s: **no blocker**, F1–F5 closed and Manager latency evidence accepted within its stated limits. It answered correctness, elegance and data-structure use positively. Verbatim review:

<details>
<summary>Heap round-two verification</summary>

# Focused re-review: S1 heap reclamation, closure of F1–F5 and the Manager latency gate

**Verdict: no blocker.** I approve the implementation for this unit. This doesn't settle the project-wide gates or doc reconciliation, which the parent owns.

## What I verified directly
I followed the AGENTS.md read order where it applies to this slice, then read the code and tests the claims depend on:
- **Production source:** `weft/core/family_scheduling.py:99-175`. Line 133 now reads `request_id not in seen and self._owns(...)`, which matches the embedded delta.
- **Locality test:** I traced `tests/core/test_family_scheduling.py:416-479` by hand against the final source:
  - Removing 300 and 301 doesn't trigger compaction (5 > 8 and 5 > 6 are both false). Removing 302 does (5 > 4).
  - `remove_pending` uses `dict.pop`, which isn't probed. So the probes come only from `_compact`: 5 pending probes and exactly 2 index probes (303, then 999). The calls from `remove_pending` add 0 owner steps each, and 999 adds `depth`, so `sum(owner_steps) == depth` holds.
  - `owner_steps[-1]` comes from the `original_handle.owner()` call at line 475, after the handle was compressed, so `<= 1` is correct.
- **Manager call site:** `weft/core/manager.py:5761-5780`. The exact `move_one` happens first. Then the absent-source path runs (`peek_one` is None, the label is deleted), and `remove_pending` is called after the broker `with` block. So the pause lands on the reactor turn without holding a broker connection, as the gate claims.

I didn't open or rerun the `/tmp` scripts or logs. The evidence below is judged from the embedded transcripts and checked against the code paths.

## Closure of F1–F5

| ID | Status | Basis |
|---|---|---|
| F1 | **Closed** | The raw row is internally consistent. The 20,001 owner calls are 10,001 removal-loop calls at 0 edges plus 10,000 compaction calls. The 10,063 edges are 64 for the first deep lookup plus 9,999 one-edge lookups after compression. H = 20,001 > 2·10,000 is exactly the trigger. The row ran on the `1f7b` snapshot, but the only source change since then is the reorder at line 133, which changes nothing when all IDs are distinct, so the counts carry over. The record correctly states this is instrumented and taken under PG load. |
| F2 | **Closed** | Each mutant fails at the assertion I would predict from the code: <ul><li>Dropped physical-count transfer: `4 == 7` at line 368.</li><li>Owner looked up before membership: `5 == 2` at line 472. The old set assertion at line 473 would also have caught it, because `get(300)` records a probe even though it misses.</li><li>Global scan: `tuple(pending)` hits `__iter__` and raises.</li><li>The four replayed controls fail at lines 245, 301, 330 and 348.</li></ul>The normalized test identity (`4492ed23…`, empty diffs) settles the earlier concern that the test copies weren't tied to the repository file. |
| F3 | **Closed** | <ul><li>Exact commands and hashes are recorded.</li><li>Scoped `ruff check`, `ruff format --check` and `mypy` are clean on the final hashes, including the `dict.get` override and the `NoReturn` overrides.</li><li>The failed `sorted(list[object])` attempt is kept and correctly replaced.</li><li>The 44-test family gate predates F4/F5. The final focused run is 24 tests.</li><li>The full 478-file mypy run is a parent result, and I don't treat it as proof for the final hashes.</li></ul> |
| F4 | **Closed as specified** (wording nit, see N1) | The order is swapped and the comment says "at most once". No test kills the reversed order, because the locality fixture has distinct IDs. That's acceptable for a constant-factor nit, and adding a test just for it would be needless machinery. |
| F5 | **Closed** | `len(index_view.probes) == 2` together with the set check catches any repeated resolution of an ID, without sorting `object` keys. |

## Manager latency gate: passed
- **Real path.** The staged path is the same one that causes the S1 defect:
  - Sibling borrowing takes the oldest rows through family FIFO, which leaves non-root tombstones in the bucket.
  - L absent-source cleanups through the real `_fetch_next_message` take the bucket to H = 2L+1 with L+1 live entries.
  - One ordinary exact reservation then triggers compaction at H > 2L.

  This matches the reported 20,001 → 10,000.
- **Ordering.** The reservation and hint removal happen before compaction, and the observer asserts both.
- **Timings.** At L = 10,000, compaction took 5.4 ms and PONG arrived in the same turn after 7.4 ms. At L = 100 the figures are 0.1 ms and 3.0 ms.
- **Conclusion.** Under this load, local compaction doesn't cause an unacceptable control delay. Task 3's fallback to incremental reclamation isn't triggered.
- **Limits.** As the record says, this is not a universal bound and not a natural 20,000-process workload.
- **Failed first attempt.** It is kept, its cause is identified (registering the in-process Manager's TID with the harness), and the retry follows the existing `startup_pg_manager` cleanup pattern without loosening any deadline. That's handled correctly.

## Nit
**N1 (nit, optional):** The closure summary says F4 avoids "repeated owner resolution for duplicate IDs". That is true only for IDs that end up retained. `seen` records only owned IDs, so a duplicated ID that is pending but owned by another bucket still resolves once per node.

Example: 200 is removed and re-added in bucket 100, removed again, then re-added under a recovered parent 150. Bucket 100 now holds two stale 200 nodes, and each one resolves its owner during compaction.

The cost is still bounded by H, so the accounting is unchanged. The code comment at line 469 ("each *retained* ID") is accurate.

Suggested disposition: correct the wording in the record. Optionally, add the ID to `seen` before the ownership check, which makes "each ID at most once" true at no extra cost. No test needed.

## The owner's questions
- **Is it correct?** Yes. H ≤ 2L holds at every operation boundary. Both compaction and pruning use the current-owner predicate, and every control mutant is killed at the expected assertion. Leftist meld, FIFO/round-robin/LIFO order and broker custody are untouched.
- **Is it elegant?** Yes. It is textbook lazy deletion with global rebuilding: rebuild once more than half the nodes are stale, then heapify from singletons with bottom-up pairwise meld in O(H). It adds one counter and one local `seen` set, and reuses the existing union-find owner handles for liveness. There's no generation store and no second index. I'd be comfortable keeping it.
- **Does it use the data structures properly?** Yes. Node reuse is safe because each node belongs to exactly one heap. `_meld` recursion depth is bounded by the right-spine rank, about log₂ H. Path compression without union-by-rank is honestly described as amortized, not constant. Nothing in this delta should be removed.

## Observations (not actionable for this unit; the parent owns these)
- In `docs/plans/2026-10-27-runtime-and-test-repair-plan.md`, two execution-record entries are stale relative to this evidence:
  - Line 829 still says F4/F5 focused verification is pending.
  - Line 818 says the earlier four control snapshots "differ only by … tuple type annotation". The final manifest now says all seven normalize byte-identically.
- The plan doesn't record the Manager latency-gate result or the attempt-1 note yet.
- The [MA-1.8] implementation mapping (`03-Manager_Architecture.md:563`) is consistent with the final code.

</details>

N1 disposition: accept the wording correction only. Deduplication avoids repeated owner resolution for retained IDs; cross-owned stale duplicates can still resolve per node, within the stated H-based accounting. No further implementation change is justified. Earlier pending-verification entries above describe their historical stage; the final 24-test gate, seven byte-identical control snapshots and full mypy success now supersede them. The comment that the plan lacks Manager gate/attempt-one evidence is not current: both were already recorded under “S1 retention and Manager latency evidence” before this re-review. This is bucket-local rebuilding, not a scan of the global pending population.

The corrected full PostgreSQL suite passed **5,909 tests**, with 27 backend/platform/live-provider skips, in 341.05 s at normal configured concurrency and server capacity. `uv sync --all-extras --frozen` restored the repository environment afterward. The only subsequent runtime edits are the review-requested negative Windows EIO test and comments/scenario IDs, whose focused proof and CI remain pending.


### Author fresh-eyes repair integration pass (2026-10-28)

A distinct post-correction pass traced Host and Docker callers through the shared supervisor, every return/exception/finally path, real stream ownership, the heap's add/meld/remove/prune/rebuild accounting, and the test-only validation/clock changes. This is an author pass, not independent or cross-family review; the final combined pass after admission work remains required.

Correctness: the post-spawn scope owns all acquired resources, stops all adapters before joining, and preserves the invocation's primary failure. Monitor construction errors still propagate, ordinary monitor protocol failures retain their existing tolerance, and runtime hooks retain Docker ownership. Cancellation remains ahead of root/timeout decisions; root exit now reaches the same bounded drain as other terminal paths. Full captured output is no longer throttled by idle sleeps while queues contain work. Heap counts transfer through meld and remain bounded after local reclamation; current ownership and pending membership jointly exclude stale re-additions. No queue reservation, TaskSpec transition, public API or admission authority is changed by these repairs. Local execution evidence supports these conclusions; actual Windows syscall behavior is still unverified until CI.

Elegance: the correction uses one acquisition/rollback owner and one small pipe adapter rather than an interactive-session abstraction or platform framework. The adapter reuses standard incremental codecs/newline decoding, replacing the custom decoder. The existing Host/Docker supervisor and stop/kill hooks stay shared. The one local exception flag and two queue-empty idle hints express the exact boundary without a new control framework. The tests retain independent literal transition/action expectations and replace broad global clock mutation with owner-local bindings.

Data structures: lazy deletion plus geometric bucket rebuilding preserves cheap leftist meld and uses bottom-up pairwise meld rather than repeated global unions. The separate indexed FIFO is unchanged. Owner-handle path compression is reused with its actual cost disclosed. Queue.empty is only an idle hint for the sole consumer, never a scheduling authority or dequeue decision. The new test matrix uses literal expected acceptance, so it does not compute its own expected result from the production predicate.

Counterarguments retained: portable nonblocking adapters poll idle pipes at the existing 10 ms floor, adding idle wakeups compared with uninterruptible blocking readers; local compaction has an O(L) pause; arbitrary callbacks cannot be preempted, and the final 0.25 s drain can still omit output beyond its budget. Unbounded captured-output queues predate the repair. These are real limits, not claims of an ideal scheduler or universally bounded cleanup latency. No additional architecture is justified within these confirmed repairs.


Runtime R2-1–R2-3 closure: the explicit platform/error matrix now includes Windows EIO rejection with exact exception identity and owned-resource cleanup. The existing five output/error cases pass in 1.91 s. A platform-only suppression mutant still passes the old two matrix rows (1.03 s), but the new Windows EIO row fails “DID NOT RAISE OSError” (0.98 s). Full mypy passes 478 files; Ruff/format and whitespace checks pass. The only production edit since the passing full suites is the single-consumer explanatory comment. Separate focused round-three review and integrated repair review are pending.

CI sequencing: open a draft pull request for this locally verified repair snapshot while integrated review runs, to execute the owner-required Windows jobs. Neither draft publication nor CI startup closes this plan. Repair acceptance still precedes admission measurements; concurrent launch workers remain deferred.


### Integrated repair Tier-1 review: drain semantics under reconsideration

Contained Claude review completed successfully in 411.21 s, verdict **blocker: F1**, with F2 also requesting owner disposition. The reviewer flags a potential conflict between bounded output-delivery time and already-captured output, plus the Consumer's logical stream-finalization boundary. No runtime edits were made in response. Actual Consumer/broker reproductions and the governing stream contracts are being checked before accepting the claims. Verbatim review:

<details>
<summary>Integrated repair findings and three-question assessment</summary>

# Integrated review: Task5 repair unit (e220748f → 4c490043)

**Verdict: blocker: F1.** The heap change, the test repairs, and most of the runtime change are correct. F1 is a new regression where a command that exits cleanly can lose output Weft has already read. F1's fix changes the meaning of a spec sentence that has already been promoted, so it is flagged RECONSIDERATION REQUESTED.

**What this review is based on:** I read the source myself at the current checkout; the tree is clean at HEAD 4c490043. I did not run any tests and did not recompute file hashes. Execution results come only from the logs you supplied. Windows CI, Linux-owned extension CI, and PR48 run 40699715283 are still pending. Nothing here certifies them.

## Findings

| ID | Severity | Location | Causal evidence | Suggested disposition |
|---|---|---|---|---|
| F1 | [P1] | `weft/core/runners/subprocess_runner.py:207-209`, `:335-344`, `:620-622`, `:653-675`; `weft/_constants.py:1921` | **Mechanism.** The supervisor now stops its main loop as soon as the root process exits. It then gets one 0.25 s budget (`SUBPROCESS_STREAM_DRAIN_TIMEOUT`), and the new deadline check (`:621`) stops pulling chunks off the local queues when that budget runs out. But the reader threads drain the pipe into an *unbounded* queue, so a fast child is never slowed down by how quickly callbacks run. At root exit, the queue can therefore hold a large backlog that has already been captured, with the reader's EOF marker queued behind it.<br>**Why callbacks are slow in practice.** With Consumer live streaming, each callback posts to a worker-result queue capped at 256 entries (`base.py:805-812`). The reactor turns each entry into an outbox broker write (`consumer.py:581-603`), so callbacks run at broker speed.<br>**Result.** A command with no descendants that writes a few MiB, or many small flushed chunks, faster than the broker can absorb them comes back `ok` with truncated `stdout`. Its final callback marker is never sent, even though the reader did see EOF.<br>**Why this is a regression.** The old loop only broke when the root had exited *and* both streams had closed, and it drained each queue until it was empty. All captured output was delivered.<br>**Why it is new evidence.** The accepted risk was justified by descendants holding pipes open. Here there is no descendant, EOF has already been observed, and the data is already in Weft's memory.<br>**No test covers it.** `test_ready_output_drains_without_waiting_between_batches[exits-unprompted]` only uses fast callbacks. | **Fix (smallest I see):** the deadline should limit *waiting for pipe EOF*, not *processing data already captured*. At EOF or deadline, stop and join the output adapters (move stop/join into a small idempotent helper that `finally` reuses). Then drain the now-finite queues until empty. Emit final markers only where the reader actually queued `None`. A descendant still cannot extend the budget, because readers stop at the deadline. Apply the same rule to the cancel/timeout/limit paths for consistency.<br>**RED test:** a real child writes 64 × 64 KiB and exits; the callback sleeps 10 ms per chunk. Assert the full stdout and exactly one `("", True)` marker.<br>**RECONSIDERATION REQUESTED:** RM text says the supervisor "drains captured output for the bounded private stream-drain budget". It should say the budget bounds the EOF wait and that captured data is processed fully. |
| F2 | [P2] | Interaction of `subprocess_runner.py:558-562`/`:673-674` with `consumer.py:309-312` and `weft/core/outbox.py:78-87` | When a live-streaming command's descendant holds the pipes open past the boundary (the accepted R2 case), the root's `ok` outcome is committed with no stdout `final=True` envelope. Live streaming doesn't publish a separate result (`_serialized_result_bytes`). `process_outbox_message` only produces a value when it sees the final envelope. So the *entire* streamed result is lost, not just output past the boundary. Before this change that run hung until timeout, so the new behavior is a new kind of outcome rather than a worsening of an old one. | **Owner decision, RECONSIDERATION REQUESTED.** The plan says budget exhaustion must not be treated as EOF. Options:<br>(a) Separate "stream complete" from "pipe EOF": the Consumer emits the stdout terminal envelope when it commits an `ok` live-stream outcome that never received one.<br>(b) Accept and document the risk.<br>Fixing F1 does not change this case. |
| N1 | nit | `subprocess_runner.py:570-579` | The input writer uses `SUBPROCESS_STREAM_READ_SIZE` both as its character-chunk size and its write-slice size. | Optional: add a short comment, or reuse it under a neutral name. |
| N2 | nit | `family_scheduling.py:124` | `heads: list[_Head \| None]` exists only because `_meld` returns an Optional. | Leave as is, or narrow the type after the first pairing. |

## Answers to your three questions

### 1. Is the implementation correct?

**Heap: yes.** I traced add, `remove_pending`, `remove`, `candidate`, `merge`, and `_compact`:
- **Counts.** H (stored nodes, `physical_count`) is exact under add, root pop, stale pruning, rebuild, and merge. L (live entries, `count`) equals the number of pending IDs whose handle resolves to the bucket, because `remove_pending` decrements the resolved owner.
- **Bounds.** H ≤ 2L holds at every public operation boundary. Merge preserves it by summation, so contraction needs no scan.
- **Order.** Rebuilding with pairwise leftist meld keeps heap order, so FIFO/LIFO selection is unchanged.
- **Ownership.** `_owns` (pending *and* current owner) closes the late-recovery case where the old bucket offered a re-added ID. Old stale/new live duplicates after a later meld are handled two ways: candidate returns the right request, and compaction deduplicates.
- **Locality.** Only point lookups, so no global scan. That matches [ALT-WT-016].
- **Custody.** The broker reservation path is untouched.

**Test repairs (T1–T4): yes.**
- Forbidden pairs now go through the real `validate_task_status_transition`.
- The retained spec table carries literal action/ID/target. The duplicate core table was removed without losing assertions, and the generic `StateMachine` coverage checks are kept.
- The `TaskSpec` same-state and terminal checks compare whole state.
- The T2 observation seam wraps the real resolver, with a competing executable on `PATH` as a trap.
- T3 uses a module-local `time` facade.

**Runtime: correct except at the drain boundary (F1, F2).** Correct parts:
- The clock starts before input delivery.
- Input backpressure can no longer block supervision.
- Root exit is accepted separately from EOF.
- One `except BaseException` owner handles rollback, and it runs before `finally` stops and joins the adapters. So readers see EOF from the killed tree, and the primary exception stays primary.
- Monitor load/type errors propagate; ordinary monitor errors are still tolerated.
- Adapters registered before `start()` are joined, including an interrupted start.
- Each adapter closes its own endpoint. Close never contends with a blocked buffered operation, because no buffered I/O is ever used.
- An adapter error after an `ok` return is re-raised from `finally`, so a failed adapter cannot report success.
- Decoder and newline handling matches `TextIOWrapper(newline=None)` on both read and write, including the Windows LF→CRLF translation.

Unverified on Windows: CPython's nonblocking-pipe `os.read`/`os.write` errno behavior and zero-length writes. This rests on the pending CI.

### 2. Is it elegant — would an expert systems programmer be proud of it?

- **Heap: yes.** It is textbook lazy deletion with global rebuilding: rebuild once more than half the stored nodes are stale, and the cost is paid for by the discarded history. The rebuild is a linear bottom-up meld that reuses the existing nodes, and owner handles reuse the existing path compression. It adds about 60 lines and no new structures. The cost claims in the docstring are honest.
- **Runtime: mostly.** `_PipeAdapter` is a small, well-bounded primitive. It reuses `io.IncrementalNewlineDecoder` and the incremental codecs, and it deletes a hand-written CR/LF state machine along with its C901 suppression. Two things keep it from being exemplary:
  - The drain semantics bound the wrong quantity (F1).
  - The supervisor function is still about 370 lines, with three near-identical terminal branches. That predates this change and wasn't worsened.

  The 10 ms stop-event polling per pipe is a plan-approved trade-off for Windows parity, not a defect (see Observations).

### 3. Does it use the underlying data structures properly, or overcomplicate things?

- **Heap:** proper use. There is no second liveness index or generation store. The temporary `seen` set is local to the bucket and discarded afterwards.
- **Runtime:** the queue-plus-finite-batch structure is right. The mistake is putting the deadline on dequeue instead of on the EOF wait. The fix makes it simpler, not more complex.

## Integration and preserved differences

**Preserved:**
- **Host vs Docker.** Both still have distinct stop/kill hooks, using `terminate_process_tree`/`kill_process_tree` on host and container stop/kill on Docker. Docker's outer `_cleanup_process`/`_remove_container` is idempotent after the supervisor's close and reap, so it doesn't compete as a second owner. Its `poll()` guard makes it a no-op.
- **Interactive sessions.** Their lifetime, API, and blocking `readline` readers are unchanged.
- **Monitor errors.** Fatal vs ordinary handling is preserved.
- **Consumer failure path.** The test proves the real failure path: `builtins.str` is rejected by real validation, the task ends `failed`, KEEP/CLEAR reserved policy is applied, and the child is reaped by the runner rather than by a second Consumer owner.
- **Heap integration.** The only caller is `manager.py:5780`.
- **Traceability.** Spec → mapping → symbols match: `_PipeAdapter`, `_drain_*`, `_PendingIndex.remove`/`_compact`/`_owns`. The registry swaps RUFF-SUP-037 for RUFF-SUP-354 consistently with the policy-test inventory.

**Changed:** the outbox at the drain boundary, covered by F1 and F2.

## Observations (not actionable this pass)

- **Idle polling cost.** Each idle pipe wakes every 10 ms; that's up to about 300 wakeups/s per running command, where the old daemon readers had none. On POSIX, selectors with a self-pipe wake would avoid this. Owner decision if large fleets of mostly idle commands matter.
- **Unbounded joins.** `finally` uses `join()` with no timeout. That is correct given nonblocking I/O, but an asynchronous `KeyboardInterrupt` arriving mid-join would skip the remaining joins and closes.
- **Pre-existing:** unbounded local output queues, which are an accepted risk and part of the cause of F1.

## Evidence status

| Source | Status |
|---|---|
| Local full SQLite suite | 5958 passed, 49 skipped |
| PG suite | 5909 passed, 27 skipped (log excerpt) |
| mypy / ruff / docs | Clean |
| Django / Docker / macOS / microsandbox | Clean |
| Runtime-round3 and heap/test-repair sensitivity controls | As summarized in the supplied logs |
| Windows, Linux-owned extension CI, PR48 CI | Pending; not passed |

</details>

Initial disposition: investigation pending. A slow synthetic callback alone does not establish a new finding beyond the explicitly accepted bounded-drain risk noted in the earlier runtime review. The additional claim about actual Consumer queue backpressure and public result completion must be demonstrated. F2 must distinguish physical pipe EOF from completion of the captured logical outbox stream, whose finalization is required before task completion by [MF-5]. If the proposed fix reverses the promoted [RM-5.2] budget rule, the review-loops runbook Feedback Loop step 5 requires a human decision before changing that rule. Admission work remains behind repair acceptance.


### Integrated review reproduction and reconsideration (2026-10-28)

F1 is confirmed through the actual Consumer, its 256-slot worker-result queue,
and SQLite broker, without artificial callback sleeps or altered chunk sizes.
The 32 MiB control completed intact. At 128 MiB, the repaired supervisor captured
all 134,217,728 characters but returned only 81,330,176; 808 queue items remained,
including the genuine EOF marker. The public result was `completed` with
`value=None`. The exact e220748f supervisor, explicitly rebound into HostRunner,
delivered all 134,217,728 characters and a final frame. Thus this is actual
captured-data loss under ordinary Consumer backpressure, not merely the already
accepted possibility of missing future descendant output.

F2 is separately confirmed with 14 characters of root output and a silent
inherited-pipe descendant: all data was delivered, but no final frame was
published, and the public completed result again had `value=None`. Root and owned
adapter cleanup succeeded. Physical EOF and logical output finalization must
remain distinct. The governing completion-order sentence is [MF-3]; [MF-5]
requires producer-owned stream envelopes and does not authorize a reader to
invent an end frame. The earlier disposition's attribution of the ordering
sentence to [MF-5] was imprecise.

Evidence lives under `/tmp/weft-implementation-20261027/`:
`runtime-integrated-stream-flood-current-134217728.json`,
`runtime-integrated-stream-flood-baseline-134217728.json`, and
`runtime-integrated-stream-inherited-current.json`. Current supervisor source
SHA256 is c156dcd08e4770eb2c0588b2f1bdad0b7b6c9b8be84f93e6eb74a73b09a6c54a.
These are diagnostic probes, not yet committed regression tests.

**RECONSIDERATION REQUESTED; owner decision pending.** Proposed correction:
bound further pipe capture/waiting by the private drain budget, stop and join
readers to freeze the finite captured backlog, then deliver that backlog fully.
Reuse the same stop/join ownership operation on normal and exceptional cleanup.
Consumer must finalize the logical stream before publishing its completion,
without manufacturing physical EOF. This changes the promoted [RM-5.2] rule
that bounds delivery of already-captured output. Large captured results or slow
callbacks may extend final delivery beyond 250 ms. No production change has been
made pending the human decision required by Feedback Loop step 5 of the review
runbook. Admission measurements remain gated on repair acceptance.

CI at 4c490043: 31 checks succeeded, one failed, five skipped. The failure is
Windows/Python 3.14 `test_manager_autostart_ensure_restarts_after_abrupt_child_kill`:
its first autostart-spawn observation expired before the kill/restart stage.
Cause remains under investigation; neither a timing flake nor an unrelated
failure is established. Run 40699715283, job 122856145718. Skipped dependent
extension checks are not passes.


### Runtime round 3 closure record (review completed before integrated review)

Claude Opus 5.5, Tier 1, 45.56 seconds: PASS for R2-1/R2-2/R2-3.
This scoped pass does not close the later integrated F1/F2 findings. R3-1 optional
test-name nit declined: the existing name remains accurate and its docstring
and explicit three-row matrix state both conditions. No behavioral ambiguity
justifies another edit.

<details>
<summary>Verbatim cross-family runtime round 3 review</summary>

**Findings**

| ID | Severity | Location | Finding | Suggested disposition |
|---|---|---|---|---|
| R3-1 | nit | `tests/core/test_subprocess_runner.py:1539` | The test is still named `test_closed_input_einval_is_only_ignored_on_windows`. The new `windows-eio` row tests the other half of the rule: on Windows, only EINVAL is ignored. The name now covers only the platform side. The docstring at `:1546` ("Partition … from real I/O faults") already describes both sides, so nothing is wrong. | Optional. Declining is fine. If the test is renamed, something like `..._only_windows_einval_is_ignored` fits. Not a blocker. |

The fixes introduced no new defects.

**Verification per ID**

- **R2-1: verified.**
  - **Production check:** the rule is still `sys.platform == "win32" and exc.errno == errno.EINVAL` (`subprocess_runner.py:589`), and it is the only use of `sys` in that module. So swapping in `SimpleNamespace(platform=...)` is still safe.
  - **Expected outcomes are written out:** each row states its result as a literal `ignore_error` value (`:1534-1536`). The test never recomputes the production condition.
  - **Three cases are enough:** for a two-part "A and B" rule, three cases (both true; platform false; errno false) isolate each part. With them:
    - removing the errno part fails `windows-eio` (your control shows exactly this);
    - removing the platform part fails `non-windows-einval`;
    - turning `and` into `or` fails both negative rows;
    - forcing the condition to false fails `windows-einval`.

    A fourth `linux/EIO` row would catch no additional mutant, so leaving it out is right.
  - **Assertions:** both negative rows check `caught.value is failure`. This works because `_raise_pipe_errors` re-raises the stored instance (`:602`), and each row builds its own `failure`. The real process, the adapter ownership, `write_attempted` and `_assert_owned_pipes_released` are all unchanged (`:1569-1588`).
  - **Portability:** `errno.EIO` exists on Windows, so the parameter table imports cleanly on every platform.
  - **Fits the plan:** this matches the plan's disposition (plan `:919`). There is no new helper or framework.
- **R2-2: verified.**
  - **Sole consumer holds:** the two queues are local (`:138-139`). The adapters only `put` (`:557`, `:561`), and only the supervisor thread calls `get_nowait` (`:624`).
  - **Next-pass progress holds:** a queue that is not empty at `:332` stays non-empty, so the next pass drains at least one item at `:161-172`.
  - **Race cost holds:** a `put` that lands after an empty check costs at most one `sleep_for`, which is never more than `ACTIVE_CONTROL_POLL_INTERVAL`.
  - **No code change:** the guard itself is unchanged; only the comment moved from producer to consumer reasoning.
- **R2-3: verified.**
  - **Output cases:** `exits-unprompted` is the case where the child exits without waiting for the release file. `awaits-release` is the case where the loop at `:1403-1406` makes it wait.
  - **Matrix rows:** `windows-einval`, `non-windows-einval` and `windows-eio` name the injected branch, not the host OS. That fixes the original complaint that "linux" ran on darwin. Using `non-windows` instead of the suggested `posix` is more accurate, since the branch is just "not Windows".

**Evidence check**
- The current file contents match the embedded diff exactly at `subprocess_runner.py:330-333` and `test_subprocess_runner.py:1374-1376, 1531-1588`.
- I couldn't recompute the SHA256 hashes with read-only tools, so the hash claims rest on your embedded evidence.
- The mutant script swaps exactly one occurrence of the production condition and asserts the source hashes are unchanged before and after. The mutant results are consistent with the analysis above.

**Verdict: PASS** for R2-1, R2-2 and R2-3.

**Your three questions**
1. **Is it correct?** Yes. The new row catches the one mutant that used to survive, the comment's reasoning is accurate, and the labels match what each case does.
2. **Is it elegant?** Yes. It is a minimal extension of the existing parameter table: one new row, explicit expected outcomes, and the real-process scaffolding left alone. The name nit (R3-1) is the only rough edge.
3. **Does it use the data structures properly?** Yes. The two-queue, single-consumer design is unchanged and is now described correctly. The test table is a flat set of parameters with nothing added.

**Observations (not actionable this pass)**
- `_drain_streams_until_closed` (`subprocess_runner.py:671`) uses the same empty-check-then-sleep idiom without a comment. That predates this delta and is a cosmetic consistency point only.
- The open risk you already accepted still applies: the injected platform branch is not proof of real Windows behaviour, so Windows CI remains the required acceptance gate.

</details>


### Windows failure disposition, bounded investigation (2026-10-28)

Root cause remains unknown. The first-spawn assertion expired before child kill;
therefore the run does not demonstrate broken restart behavior. The fixture is
a function target and does not enter the repaired command-pipe supervisor.
Manager and this test are unchanged from e220748f. Neither fact rules out shared
load or exposed timing behavior. The old successful Windows 3.14 run used a
different Python patch release, pytest release and runner image, so it is not a
controlled baseline. Same-head Windows 3.12 and 3.13 core shards passed.

The failed run lacks per-turn timing, pending/reserved custody, admission and
launch-receipt state before cleanup; those are the discriminating observations
if failure repeats. Do not increase timeouts, reduce concurrency or declare a
flake from the current evidence. One same-head, unchanged failed-job rerun was
dispatched (run 40699715283 attempt 2). A ten-minute thread heartbeat monitors it
and dependent checks, stays quiet on unchanged status, and stops on completion.
Evidence and full read-only diagnosis: `/tmp/weft-implementation-20261027/windows314-autostart-diagnosis.md`.


### Concrete pending correction for owner review

This is a proposed replacement, not an approved change to the operative repair
steps or spec. The preferred algorithm separates capture from delivery using
existing thread joins: after accepting root completion (or selecting a terminal
stop/kill path), wait for output reader completion against one shared absolute
capture deadline. Then signal every acquired adapter to stop before joining any
of them. Reuse that idempotent stop/join operation in exceptional cleanup. With
producers joined, repeatedly alternate the existing finite per-stream drain
helper until both local queues are empty, without sleeps or a delivery deadline.
Only reader-produced None markers count as physical EOF. This avoids a new
watcher, deadline thread or second output store. Callback exceptions preserve
existing primary-exception cleanup and do not promise delivery to a failed sink.
The budget starts when completion is accepted, not retrospectively at OS exit;
callbacks already blocking the supervisor remain outside a preemption guarantee.

Proposed [RM-5.2] replacement, subject to owner decision:

> Root process completion and output-pipe EOF are separate observations. Once
> root completion is accepted, the supervisor uses the bounded private
> stream-drain budget to await output EOF and finish accepting output from
> inherited pipes. When both output adapters finish or that budget expires, it
> stops and joins its owned I/O adapters and closes their owned endpoints. This
> freezes a finite set of captured output. The supervisor delivers that captured
> output before forming its returned outcome; expiry must not discard output
> already captured in its local queues.
>
> The capture budget is not a total result-delivery or teardown timeout. Delivery
> duration depends on captured volume and sink throughput and may exceed the
> private budget. Output produced after capture is stopped is not promised.
> Budget expiry neither converts a completed root into an execution timeout nor
> asserts physical EOF. Missing physical EOF remains a bounded diagnostic, with
> no new terminal state or outcome field.

Apply the same freeze/delivery owner after cancellation, timeout and limit hooks,
while preserving their outcomes. Do not promise broker delivery through an
already-stopped Consumer publisher. For successful live command outcomes,
Consumer's existing per-work-item callback closure can track each final frame
and publish a missing logical final through the same event publisher before
returning its successful work result. Preserve indices, stdout strip transform,
and exactly-once finals. Do not synthesize physical EOF, alter result readers,
or emit finals unconditionally during exception unwinding. Non-ok logical-seal
policy is not expanded by this success-result repair.

Verification after approval: real Consumer/public result checks for exact large
output and empty/nonempty inherited-pipe results; controlled finite captured
backlog with slow delivery; genuine EOF exactly once; continuing descendants
stopped at the capture boundary; all terminal branches and callback failure
cleanup; removal mutations that recreate lost data/missing final. Replace the
old delivery-deadline test as a superseded contract test, not as a tautological
or bad test. Re-run appropriate runtime/Consumer/Docker suites, integrated
review and platform CI. Existing full-suite passes predate this proposal.

An uninstrumented 128 MiB run independently returned only 70,385,664 characters
and public `value=None`, ruling out observer wrappers as the cause. The default
10 MiB output_size_limit_mb is a spill threshold, not a total live-stream cap;
individual live frames use the existing streaming path. All probe roots,
descendants, adapters and harnesses were cleaned up, with source unchanged.
Full disposable proposal and evidence inventory:
`/tmp/weft-implementation-20261027/runtime-integrated-reconsideration-proposal.md`.


### Owner approval and promotion of capture-boundary revision (2026-10-28)

After the explanation of where, when and how the 250 ms budget applies, the
owner replied: “Ok, this sounds reasonable”. This approves the proposed RM-5.2
revision: bounded further capture, full delivery of captured output, and the
explicit tradeoff that large output or slow sinks can extend delivery beyond
250 ms. The reconsideration is resolved. The operative spec delta and Task 2
instructions above were updated with this promotion, not only this log entry.
Implementation and regression/review gates remain open. Earlier pending records
are historical and do not supersede this approval.

CI run 40699715283 attempt 2 at4c490043 completed successfully, including
Windows 3.14/core-commands and all four dependent extension/integration jobs.
The unchanged rerun establishes intermittence, not the cause of attempt 1's
autostart failure. No timeout or worker-count change was made. The completed-run
monitor was deleted; the new correction requires its own CI and review gates.


### Approved correction: pre-implementation cross-family review

Claude Opus5.5 Tier 1,313.18 seconds: no blocker. C1/C2/C4 accepted and applied
to operative Task2: finalized adapter-error check before delivery; frozen loop
ends on queue emptiness; returned-outcome callback invariant and joint landing.
C3 accepted: controlled sink is mutation proof,128 MiB is integration evidence
with exact length/hash. C5 accepted for symbol reconciliation after implementation.
C6 accepted for missing-final ordering (stderr before stdout); no claim changes
pre-existing genuine EOF ordering. C7 already satisfied by the terminal-path
parameter table, including nonzero-root error. Duration continues to include
delivery as the existing calculation dictates; backend callback support outside
Host/Docker is an out-of-scope observation, not new fallback behavior.

RED:10 cases,8 intended failures/2 controls pass in 1.59 s. Five terminal-path
rows lost stdout; late callback error did not fire; two inherited-success rows
had zero final frames. PhysicalEOF and failed-root no-seal controls passed.
The timeout row was then made readiness-gated using an owner-local clock facade,
without mutating stdlib time; its isolated RED still failed exact-output equality
in 2.04 s. No setup/cleanup failures. Logs: runtime-capture-red.log and
runtime-capture-timeout-red.log under the implementation evidence directory.

<details>
<summary>Verbatim capture-boundary design review</summary>

# Review: approved capture-boundary correction (repair 4c490043)

I checked that every cited identifier exists: `run_monitored_subprocess`, `_PipeAdapter`, `_drain_stream_queue`, `_drain_streams_until_closed`, `SUBPROCESS_STREAM_DRAIN_TIMEOUT` (`_constants.py:1921`, 0.25), `Consumer._run_task_for_reactor`, `_emit_live_stream_chunk`, `BaseTask._publish_worker_result`, `process_outbox_message`, and the [MF-3] text at `05-Message_Flow_and_State.md:287-293`. The cause of F1 is confirmed in the source:

- After the root exits, `_drain_streams_until_closed` (`subprocess_runner.py:335`) runs callbacks under a 250 ms deadline (`:621`, `:654`).
- `finally` then stops the adapters (`:431-435`) and throws away whatever is still in the local queues.

`docs/program-theory.md` [WT-6] has no rejected alternative that covers this algorithm.

## Findings

| ID | Severity | Location | Evidence | Suggested disposition |
|---|---|---|---|---|
| C1 | minor | Plan Task 2 `:338-347`; `subprocess_runner.py:160,174,442` | **The plan doesn't say when adapter errors are raised in the new final phase.** The running loop checks `_raise_pipe_errors` before every drain. In the new frozen phase the first check is in `finally`. So a reader that fails during capture would first push the whole frozen backlog into callbacks (writing outbox frames), and only then raise. If a callback fails during that delivery, its exception replaces the adapter's. | Call `_raise_pipe_errors(adapters)` once, right after the capture-boundary stop/join and before delivery. Every adapter outcome is final at that point, and this matches the loop's existing check-before-drain order. No new mechanism. |
| C2 | minor | Plan `:155` (Difference Register: "Reuse `_drain_streams_until_closed`"); `subprocess_runner.py:654,671` | **Reusing that helper without its deadline would hang.** Its loop runs until both EOF flags are set, and sleeps when the queues are empty. With the deadline and sleep removed, a run with no physical EOF (the inherited-pipe case) never ends and busy-spins. | Frozen delivery should stop when both queues are empty, which is reliable only because the producers are joined. Do not stop on the `closed` flags. Update the stale register row so it no longer points implementers at the deadline helper. |
| C3 | minor | Plan verification (128 MiB integration test); Task 2 `:361` | **The 128 MiB test can't reliably catch a regression.** A backlog only appears when the sink is slower than the producer within 250 ms; 32 MiB already passed on the same machine, so a faster CI host may pass even with the bug. And once the seal lands, removing frozen delivery gives a truncated value, not `value=None`, so a "missing output" check would pass. | Make the supervisor-level controlled-sink test the mutation proof for frozen delivery. Its delay must fall after root completion is accepted. Keep the 128 MiB run as recorded evidence; if it is committed, assert only the exact hash and length, and don't count it as a mutation proof. |
| C4 | minor | Plan Task 2 `:349-354` | **The seal relies on an invariant the plan doesn't state, and depends on F1.** The invariant: on any returned outcome, every collected chunk has gone to its callback. This holds because `_drain_stream_queue` appends a chunk and then calls the callback, so a callback failure propagates instead of returning an outcome. If the seal lands without F1, today's visibly empty result becomes a silently truncated one. | State the invariant in the docstring or plan. Land F1 before or together with the seal, never the seal alone. |
| C5 | nit | `06-Resource_Management.md:150-151`; plan Comprehension Gate Q1 | The implementation mapping says `_drain_streams_until_closed` owns "bounded final capture". Under the approved design, `Thread.join` owns capture and that helper is removed or renamed ("until closed" ≠ "until empty"). Q1's expected answer "Bound stream teardown" is now "bound capture". | Update the mapping and the gate answer to the real helper names when Task 5 is reconciled. |
| C6 | nit | `consumer.py:437-461`; `commands/result.py:159-165` | `weft result` consumes only up to the last stdout final. A stderr seal published after the stdout seal would stay in the outbox on that path. The `await_task_result` path claims through the completion boundary, so it is unaffected. | Publish the stderr seal before the stdout seal. It costs nothing and keeps the stdout final last. |
| C7 | nit | Plan verification ("exercise all three paths with captured backlog") | If all four exits (ok, cancel, timeout, limit) call one finalize helper, separate backlog tests per branch just repeat the helper's proof. | Use one test parametrized over the four branches, asserting the captured output plus the owned-resource checks. Keep the existing per-branch outcome and continuous-output tests. |

## What I verified holds

- **One shared deadline on output joins.**
  - Compute `deadline` once, then call `t.join(max(0.0, deadline - time.monotonic()))` for the stdout and stderr adapters only.
  - Leaving out the stdin adapter matters. A descendant that holds stdin without reading would otherwise use the whole budget every time.
  - Joins after stop are bounded:
    - Reads are nonblocking (`:551`).
    - `put` goes to an unbounded queue and can't block (`:138-139`).
    - Every iteration of the read loop checks the stop event (`:549`).
- **Partial acquisition and error cleanup.**
  - The capture phase is only reached after every `adapter.start()` has succeeded.
  - The stop/join/close helper used by `finally` must keep the `thread.ident is not None` guard (`:434`). It is idempotent: `Event.set`, joining a finished thread, and `_close_pipe` behind `stream.closed` (`:438`) are all safe to repeat.
  - If a callback raises during delivery, it goes through `except BaseException` → `_cleanup_after_execution_failure` → `finally` with `primary_failure=True`, so the original exception stays primary.
- **No callback coupling during capture.** No callback runs while the joins wait. In the common case (no descendant) the joins return as soon as the readers hit EOF, which doesn't depend on how fast the sink is. So there is no added delay.
- **Frozen delivery is finite.** With the producers joined, the queues are a fixed set. Alternating calls to `_drain_stream_queue` until both are empty (see C2) need no sleep and no deadline. The `deadline` parameter (`:618`) and `test_stream_queue_drain_checks_deadline_after_callback` (`test_subprocess_runner.py:1224`) are then dead, so remove them as a superseded contract. Keep the fairness test at `:1101`.
- **Consumer event ordering.**
  - Stream events and the active work result share one FIFO queue (`base.py:284-285, 807`).
  - The seal is published on the worker thread before the lane wrapper publishes the result (`base.py:754-763`). So every frame, seals included, is written to the outbox before `_handle_active_work_result` commits completion ([MF-3]).
  - If the publisher has stopped, `_publish_worker_result` returns `False` at once (`base.py:805`). Frozen delivery therefore can't hang on a Consumer that is stopping.
- **Per-work-item final tracking.**
  - The callback closures are created fresh on each `_run_task_for_reactor` call. They run synchronously on the same worker thread that later seals, so there is no race.
  - The seal should reuse `_on_stdout_chunk("", True)` / `_on_stderr_chunk("", True)`, which gives the right indices and the `strip` transform (`consumer.py:601-602`) for free.
  - Restrict it to the live branch, after a normal return, when `outcome.status == "ok"`.
  - An empty stdout final decodes to `""` (`outbox.py:83-87`).

## Answers to your three questions

1. **Is the implementation plan correct?** Yes. Join-first capture with one deadline, then stop all adapters before joining any, then exhaustive delivery from the frozen queues, removes the loss mechanism without adding new machinery. The cancel/timeout/limit statuses, `duration=timeout`, and Host/Docker hook ownership are untouched. C1 and C2 are precision gaps in the plan text, not design errors.
2. **Is it elegant?** Yes. It reuses `Thread.join` as the completion primitive and the existing batch drain as the delivery step. It turns four drain call sites into one finalize helper, and the seal reuses the existing closure. It deletes code: the deadline parameter, the sleep-and-poll loop and the obsolete test. An expert systems programmer would sign it.
3. **Does it use the data structures properly?** Yes. The local queues are still the only transport. The reader-produced `None` is still the only physical-EOF signal. The logical seal belongs to the task, which owns the output's lifetime. There is no watcher, deadline thread, second store or synthetic EOF.

**Verdict: no blocker** within this scope.

## Observations (out of scope)

- `RunnerOutcome.duration` for ok, cancelled and limit outcomes now includes frozen delivery time, which depends on the sink; before, that part was at most 250 ms. No field is added. If you want duration to mean execution plus capture only, take the end time at the capture boundary.
- `supports_stream_callbacks` only inspects the backend's signature (`runner.py:176-179`). A backend that accepts the callbacks but never calls them would now produce a public value of `""` instead of `None`. In this repo only Host and Docker accept them, and both use this supervisor. The test fake at `test_task_execution.py:3987` will now get a sealed final, but it asserts only completion.
- A continuously writing descendant can add up to 250 ms of pipe throughput to the frozen backlog. That is the volume you already accepted, and the join-first form doesn't increase it: readers fill an unbounded queue whether or not anything is draining it.

The plan-file and exit-plan tools weren't available in this session, so this review exists only in this reply. I made no repository edits.

</details>


### Capture correction: implementation evidence and author fresh-eyes pass

Implemented `_finish_streams` using the existing reader thread joins with one
absolute deadline, shared `_stop_and_join_pipe_adapters`, finalized adapter-error
check before callbacks, and exhaustive alternating finite-queue delivery. Removed
the obsolete per-chunk final-delivery deadline and its superseded test. Consumer
keeps per-work final flags and reuses its callbacks for success-only missing
logical finals, stderr before stdout. No new state store, worker or public field.

Focused GREEN (normal configured workers):
`./.venv/bin/python -m pytest tests/core/test_subprocess_runner.py tests/tasks/test_runner.py tests/tasks/test_consumer_terminal_events.py tests/tasks/test_command_runner_parity.py tests/tasks/test_task_execution.py tests/commands/test_result.py tests/commands/test_consuming_result_collection.py tests/commands/test_result_settlement_policy.py --tb=short`
returned 662 passed in 32.26 s. The two Docker owner/parity modules with their normal
`-n 0` recipe returned 87 passed in 0.32 s. Full mypy:478 files clean. Ruff, format
(631 files), suppression inventory and diff checks passed. Full backend/platform
acceptance for this new correction remains pending.

Six isolated mutation controls fired on the intended assertions: discarded
frozen backlog (exact output mismatch), removed early adapter-error check
(competing callback RuntimeError replaces expected OSError), removed logical
seals (zero finals), reversed seals (broker timestamp ordering), sealed failed
outcome (one final where zero is required), skipped data callbacks (empty
callback output). Parent inspected all six failure logs, not merely exit codes.
Source/test hashes before and after were identical. The C1 test gates actual
reader dependencies until a real root poll observes exit; captured stderr
provides the competing callback, without a production-only test seam.

Real Consumer/broker evidence, no injected chunk/callback delays: corrected
128 MiB runner/direct/public values all contain134,217,728 characters with SHA256
8fb4c93e9cdd636cfe06929df7ec7cc521fe3fd3affdfe81fc13d8fcb737d26a, matching
the exact baseline. Both physical EOFs were consumed and no local backlog
remained. The complete post-terminal finalization call took0.5442435s; this
includes capture/join and delivery, not an isolated delivery measurement. The
uninstrumented repeat also returned the complete public string. Inherited-pipe
case preserved physicalEOF=false on both streams, delivered14 characters, emitted
one logical final per stream and returned the13 character stripped public value.
All roots, pinned descendants, adapters and harnesses were cleaned up. Original
reproduction artifacts are untouched. New evidence uses runtime-capture-fixed-*
and runtime_capture_fixed_* names under the implementation evidence directory.

Author fresh-eyes pass, distinct from implementation and before cross-family
closure: no new correctness finding. Checked all four terminal call sites,
shared deadline (output readers only), producer shutdown before empty-queue
termination, exception precedence, callback/result consistency, FIFO worker
events before completion, exact-once successful seals and failed-outcome control.
The design uses existing thread joins and FIFO queues with fewer timing rules;
no second lifecycle framework is warranted. Repeating idempotent stop/join in
finally is deliberate cleanup safety and costs completed-thread checks, avoiding
an extra lifecycle flag. Limits remain explicit: delivery depends on volume/sink
throughput, arbitrary callbacks cannot be preempted, unbounded local buffering
is pre-existing, and non-ok logical seal policy is unchanged. Independent
cross-family completed-work review is running; this self-pass does not replace it.


### Admission prerequisite finding S2 (2026-10-28)

Cross-family pre-B clarification review,Claude Opus 5.5,375.72s,blocked on B-F1.
Baseline owner probe confirms an expired publication retry remains selected
after broker recovery begins fresh proof grace: child timeouts are[0.0] despite
retained custody and an unexpired proof-grace interval. The first combined probe
terminated143 after writing baseline evidence; its reset arm and overall cleanup
are not yet accepted. A corrected ownership probe/control is in progress.
No Manager source change has been made. Operative S2 instructions above define
the minimal repair and proof gates; no B code or measurements are authorized
before the repaired baseline is accepted.

<details>
<summary>Verbatim pre-B cross-family clarification review</summary>

# Tier-1 cross-family review: clarification of admission-plan Task 2 / arm B

**Verdict: BLOCKED.** One finding (B-F1) blocks. It's narrow, and the fix is small. Everything else in the clarification holds up against the source at `4c490043`. With B-F1's disposition added, I'd expect a PASS; the other findings are P3 wording or simplification items.

**What I checked:** I read the named owners directly with read-only tools: `manager.py`, `base.py`, `multiqueue_watcher.py` and `service.py`, plus the named test seams. For theory and specs I relied on the embedded excerpts, which are pinned to the baseline. I didn't open the agent-context runbooks separately. No tests or probes were run, and nothing here is executed proof.

## Findings

| ID | Severity | Location | Evidence and strongest counterargument | Suggested disposition |
|---|---|---|---|---|
| **B-F1** | **P2, in-scope, blocking** | Clarified B design paragraph ("a due child-maintenance deadline … yields to an immediate ordinary turn … performs normal settlement"); proposed gate `any(remaining <= 0 for remaining in _manager_child_timeouts(...))`; source `manager.py:4533-4536`, `4545`, `4457-4462`, `8301-8305` | **The flaw:** the clarification assumes the next turn's normal settlement clears every due child deadline. One source path breaks that. **Path:** (1) A child exits with code 0. The proof read raises a broker error, so `_child_terminal_proof_visible` returns None. `_child_terminal_proof_still_within_grace` then returns False and never sets `terminal_proof_missing_since_ns`. (2) The failsafe write also fails, so `terminal_retry_after_ns = now + 15s` (`:4545`). (3) The broker recovers, the retry comes due, and the outer sweep runs. The retry check passes, but the proof read now returns False, so the grace timer starts and the sweep skips the child (`:4535-4536`). Nothing ever resets `terminal_retry_after_ns`; the only assignment is `:4545`. (4) `_manager_child_timeouts` takes the retry branch and `continue`s (`:8301-8305`). It returns `0.0` for the whole 15 s grace window, even right after the outer sweep. **Effect under B:** every spawn delivery in every turn yields for up to 15 s after the broker recovers. Baseline would keep dispatching during that window; only its timeout spin is pre-existing. That is a new dispatch stall, which the review contract forbids as a tradeoff. **Counterargument:** it needs a broker outage covering both the proof read and the write, it only affects clean exits without proof, and it is bounded to `MANAGER_CHILD_TERMINAL_PROOF_GRACE_SECONDS` (15 s). Recipe 4 kills the wrapper (non-zero exit), so it can't catch this. | **Plan sentence:** "B's child-deadline yield relies on `_cleanup_children` retiring each due deadline it services." **Owner fix:** in `_cleanup_children`, set `child.terminal_retry_after_ns = 0` once a due retry is consumed; a failed write sets it again. This also removes the existing zero-timeout spin. **Falsifiable control:** a real child that exits 0, a named outage on the proof read and the write, then recovery. Assert `min(_manager_child_timeouts()) > 0` after the next sweep, and assert a pending valid request is reserved in that turn. Removing the reset must make the test fail. Don't use a timer snapshot or a "newly due" latch instead. |
| B-F2 | P3 | Proposed [MA-3] B paragraph: "Ready child-exit evidence" | `wait(0)` reads an Event set by the sentinel observer (`:548`). The design text says a wake hint is not death evidence (WT-4, "availability and death are different questions"), but the normative sentence calls it exit evidence. | Reword to "a ready child-sentinel observation or a due child sentinel/post-exit maintenance deadline". |
| B-F3 | P3 | Design paragraph "Before a further spawn delivery"; spec "yields before further spawn delivery" | Recipe 3 fences the exit between the outer sweep and the **first** delivery, so the gate has to run before every spawn delivery in the scope. The proposed code does that, but "further" can be read as "after the first". With one authorization per turn and `_child_launch_started_this_turn` (`:1480`), the gate only ever matters for the first delivery and for non-authorizing deliveries (malformed, rejected, failed seed). | Say "before each spawn delivery in the scope, including the first". |
| B-F4 | P3 | Spec sentence "Lifecycle commits do not recurse into an active queue-delivery pass" | The scope wraps `super()._process_reactor_turn`, whose pre/post `_drain_worker_results` commit receipts and publish checkpoints inside the scope (`base.py:1488-1495`, `manager.py:1842-1844`). The handler can also call `_reconcile_launch_now` inside delivery (`:5588`). The scope flag doesn't affect any of this, but the sentence as written is broader than what's true. | Narrow it to "Child-cleanup lifecycle commits do not run inside the ordinary delivery scope." |
| B-F5 | P3 (simplification) | Proposed gate body `self._strategy.notify_activity()` | Both yield causes already wake the reactor. The sentinel observer calls `notify_activity()` when it publishes (`:548-549`, and `:515` for failures). A due child deadline makes `next_wait_timeout()` return 0 (`:8144`). The extra call is redundant but harmless, since it coalesces. | Optional: remove it, or keep it with a comment that it's a coalescing no-op. No new wake mechanism is needed either way. |
| B-F6 | P3, OWNER DECISION | Experiment decision rule: "Keep B … even if timing gain is small" | An exit that lands after the turn-start sentinel drain but before the gate now costs one extra turn before the next authorization. Baseline settled it inline. The 12-parent diagnostic spent about 74% of elapsed time inside turn spans, so this window isn't small. The cost is bounded and self-limiting (exits follow launches, at most one per turn), but it could cancel B's savings. | Count yield turns and yields per authorization in A/B. State what happens if B increases turns or latency. |

## Answers to the three owner questions

**1. Is it correct?**

Yes, once B-F1 is fixed.

- **Scope.** The scope runs after the outer sweep at `:8381` and after the forced convergence at `:8382`. It covers the BaseTask pass (`:8391`) and the explicit public fallback (`:8395-8407`). It ends before the post-delivery leadership and convergence phases (`:8414`). `process_once` rejects re-entrant turns (`base.py:1434`). Convergence and shutdown cleanup can't be reached from inside the scope: the only callers are `:8382` and `:8417`, and `_continue_shutdown_drain` / `_terminate_children` call `_cleanup_children` directly, not through the flag.
- **Exceptions.** `_timed_manager_call` uses try/finally and doesn't swallow exceptions (`:833-841`), so a `try/finally` reset of the flag keeps direct calls correct after both success and an escaping exception.
- **Gate placement.** Both routes reach `Manager._process_queue_message` before `_fetch_next_message` moves anything. The explicit drain goes through `_drain_round_robin_pass` (`watcher:1493`).
- **Yield behaviour.** On a yield the row stays in its source. Nothing is reserved, so there's nothing to requeue and STOP/custody handling is untouched. Returning False without touching `inactive_candidates` leaves the queue active, and `_update_active_queues` keeps any queue that still has pending work (`watcher:1332-1336`). The priority loop, the round-robin pass and the explicit drain all stop on zero progress (`:1534`, `:8578`), so there's no spin.
- **No latch needed.** Nothing inside the scope consumes the Event or advances child deadlines in a way that loses facts. `drain()` is called only at turn start and in shutdown. Child clocks change only in `_cleanup_children` and the sentinel drain, and any reap inside the scope is settlement, not loss.
- **First dispatch after classification** is preserved because the gate never reads `_admission_retry_timeouts` or `next_wait_timeout`.
- **Stricter than baseline.** Baseline selected and admitted the request (`:5185-5193`, `:5748-5763`) *before* its nested sweep. Gating before admission is therefore stricter than baseline freshness, not weaker.
- **Convergence clock.** The service-convergence clock is left alone, so the startup state (`_last_managed_service_convergence_ns == 0`) adds no wait.
- **The no-new-veto convergence control** kills a distinct plausible mutant (a gate that also checks convergence being due). Keep it.

**2. Is it elegant enough?**

Yes. It's one boolean with try/finally, one guarded line in the handler, and one predicate at the existing pre-reservation owner. There's no selector, helper, latch, timer store or BaseTask change. Two small improvements:

- Optionally drop the redundant `notify_activity()` (B-F5).
- Put the predicate just before the `invalid_only`/admission computation (`:5187`). That's after the cheap public startup/candidate early returns, so the O(children) walk doesn't run when nothing would be delivered, and malformed/internal deliveries are still covered.

**3. Does it use the primitives properly?**

Mostly yes. The one exception is the deadline projection, which can sit at a stale zero (B-F1).

- **Event:** `Event.wait(0)` is a side-effect-free read, and the observer suppresses repeat sentinels (`:545`), so there are no spurious repeat wakes.
- **Projection:** `_manager_child_timeouts` is reused rather than re-derived. It's an O(tracked children) integer walk, cheaper than the nested `_cleanup_children` it replaces, which made OS and broker calls. The B-F1 fix belongs in the owner, not in a snapshot.
- **Single reactor:** queue custody and one launch lane are unchanged, and the wake comes from existing sources.

## Observations (not actionable for this unit)

- **Existing spin:** the stale-retry state in B-F1 already makes `next_wait_timeout()` return 0 for up to 15 s, with busy turns, at baseline.
- **Misleading log:** a yield on the public fallback runs `_maybe_log_public_dispatch_stall` (`:8515`). That emits a rate-limited `public_dispatch_stalled` warning for what is really a maintenance yield. Every other gate denial already does the same.
- **Remaining duplicate sweeps:** the internal drains during convergence still run the handler sweep right after the pass-top sweep (`:7983`, `:8001`). This is intentional and out of scope.
- **Exit-to-zombie window:** an exit before the observer publishes leaves the Event clear. Baseline's `exitcode` probe could have caught that window. This is the disclosed, accepted finite-observation limit.
- **Line drift:** the preparation's line numbers are off by about one at a few points (8337 is really `:8338`, 8389 is `:8390`, 5461 is `:5460`).
- **Recipe 2:** S1's uncounted priority-drain loop is unchanged. Recipe 2 correctly uses a `BaseException`, but it leaves a reserved row behind that the test has to clean up.

**Verdict: BLOCKED.** B-F1 is the only blocker. The smallest fix is the "deadlines must retire after servicing" plan sentence, the one-line `terminal_retry_after_ns` reset in `_cleanup_children`, and the falsifiable control described in the table. B-F2 to B-F6 are non-blocking.

</details>


### Capture correction completed-work cross-family review

Claude Opus 5.5,Tier 1,292.03 seconds: no blocker; all three owner questions
answered yes. CB1 accepted: removed unused private timeout parameter, reading
the existing constant directly. CB2 accepted: strengthened the existing late
callback failure test with zero calls after its first exception. A retrying
mutant passes the immutable old keeper (1 pass,1.81s) but fails the strengthened
keeper at 1 == 0 (2.25s). CB3 accepted: MF-3 explicitly requires stderr-before-stdout
when both logical finals are missing. CB4 declined: equivalent flat guards are
clear and avoid another branch under the existing complexity limit; a new
suppression or extraction is not justified for stylistic nesting.

Ten focused cases passed in 3.28 s after CB1/CB2, plus targeted Ruff/mypy. Original
source/test hashes are preserved in runtime-capture-cb-controls.json; no new
test or helper was introduced. A narrow cross-family closure review of CB1–3
is running; the declined CB4 is closed. Broader backend/CI gates remain pending.

<details>
<summary>Verbatim capture correction completed-work review</summary>

**Verdict: no blocker.** I found four nits and nothing that needs a reconsideration request. The delta keeps every standing constraint and adds no new mechanism.

I had read-only tools only, so the review is in this reply and no repository file was touched. I did not re-run the full SQLite/PG/platform gates, and they are still pending. The 662 focused and 87 Docker passes cover only this delta.

## Findings

| ID | Severity | Location | Evidence | Suggested disposition |
|---|---|---|---|---|
| CB-1 | nit | `subprocess_runner.py:656` | `_finish_streams(timeout=SUBPROCESS_STREAM_DRAIN_TIMEOUT)` is a setting no caller or test changes. It was carried over from the removed helper. | Optional: delete the parameter and read the constant directly. |
| CB-2 | nit | `test_subprocess_runner.py:1657` | `fail_late` raises the same `failure` object on every call once 64 KiB is reached. A mutant that swallowed the callback error, kept delivering and re-raised the first error at the end would still pass the identity check. The plan's "never retry a raising callback" rule is therefore not checked. | Optional: count calls after the first raise and assert the count is 0. |
| CB-3 | nit | `05-Message_Flow_and_State.md:288-293` | The spec says only that "stream ordering remain[s] unchanged". The stderr-before-stdout order of the added finals is checked by the test (`final_rows["stderr"] < final_rows["stdout"]`). Readers that consume up to the last stdout final rely on that order (`result.py:159-165`; `outbox.py:81-82` returns no value for a stderr final). Today only a code comment records it. | Optional: add one clause such as "when both are missing, the stderr final precedes the stdout final." |
| CB-4 | nit | `consumer.py:479-482` | `outcome.status == "ok"` is tested twice. | Optional: use one `if outcome.status == "ok":` block with the two flag checks inside it. |

## What I checked against the code

- **Shared deadline on output-thread joins.** The deadline is computed once (`:664`). The joins cover only the adapters that have a `target_queue` (`:665-667`), so stdin is excluded and a child that never reads its input cannot use up the budget.
- **Stop all, then join all.**
  - `_stop_and_join_pipe_adapters` (`:603-609`) sets every stop event before joining any thread. It keeps the `ident is not None` guard and is safe to call twice: once from `_finish_streams` and again from `finally` (`:433`).
  - Joins after a stop are bounded: reads are non-blocking, `put` goes to an unbounded queue, and the read loop checks the stop event on every pass.
- **Adapter error before callbacks.** `_raise_pipe_errors` at `:669` runs after the final join and before any delivery. A late stdin error now goes through the primary-failure path, which calls the stop hook again under `suppress(BaseException)`. The exception raised is the same one as before, so the precedence rules hold.
- **Frozen loop ends when the queues are empty, even without EOF.** The loop at `:674` exits when both queues are empty, not on the closed flags. That is safe because both producers have been joined. There is no sleep and no deadline in the loop. Only a reader-produced `None` causes a final callback (`:632-636`), so no EOF is fabricated. The missing-EOF warning is kept.
- **All four terminal paths.**
  - Cancel, timeout, limit and root exit all call `_finish_streams` after the existing Host/Docker stop and kill hooks.
  - Statuses are unchanged, and the timeout outcome still reports `duration=timeout`.
  - Every return path goes through `_finish_streams`. The `_raise_pipe_errors` in `finally` (`:440`) is therefore effectively dead. It is cheap insurance against a future early return, so I would keep it.
- **Exceptions and partial acquisition.** `_finish_streams` is only reached after every `start()` has succeeded. Acquisition failures, failures during the capture wait and callback failures all go through `except BaseException` → `finally`, so the original exception stays primary.
- **Success-only seal: per-work flags, FIFO ordering, completion order.**
  - The flags live in the closure, which is created fresh for each work item and runs on the single worker thread.
  - The seal reuses the existing callbacks, so chunk indices and the `strip` transform come for free.
  - Seal events go through the same FIFO queue as other events, ahead of the lane result (`base.py:754-763`). `_drain_worker_results` runs before commit (`consumer.py:538-540`).
  - Deferred STOP/KILL finalizes only after the runner unwinds (`:718-723`). If the worker publisher has stopped, `_publish_worker_result` returns False, so delivery cannot hang.
  - The earlier C4 invariant still holds: `_drain_stream_queue` appends a chunk and then calls the callback, so a returned outcome means every collected chunk reached its callback.
- **Earlier review findings C1–C7.** All are addressed. The C5 gate answer now reads "Bound stream capture" (plan `:245`).

## Are the tests falsifiable?

Yes. The baseline RED run and the six mutation controls line up with the intended assertions:

- **`test_terminal_capture_delivers_backlog_after_capture_budget`.** About 128 chunks × 15 ms is roughly 1.9 s of delivery, far beyond the 250 ms budget. It checks exact output and exactly one real-EOF final. Killing the root guarantees real EOF in the cancel, timeout and limit cases.
  - `_OutputReadyClock` only delays when the timeout fires. It cannot hide lost data.
- **Adapter-error test.** It gates reads on the supervisor's own root poll, which is immediately followed by `break`. So the error and the stderr data reliably appear after the loop's last check, and the RuntimeError-replacement mutant fails reliably.
- **Consumer test.** The `physical-eof` case catches a duplicate seal (it requires exactly one final). The `failed-root` case catches a seal on a non-ok outcome. The broker timestamps are good enough to check ordering.
- **128 MiB run.** I count it as integration evidence only, as you specified, not as a mutation proof.
- **Removed `deadline-after-callback` test.** It was faithful to the old contract, and the contract changed; it was not a bad test.

## Answers to your three questions

1. **Is the implementation correct?** Yes. The separation between capture and delivery matches the approved RM-5.2 text. The physical-EOF and logical-final owners stay distinct, and exception precedence and hook ownership are unchanged.
2. **Is it elegant?** Yes. It reuses `Thread.join` as the completion signal and the existing batch drain as the delivery step. It removes the deadline parameter, the sleep loop and a duplicated stop/join block. The seal is four lines that reuse the existing callbacks. An expert systems programmer would sign it; CB-1 and CB-4 are polish.
3. **Does it use the data structures properly?** Yes. The local queues are still the only transport, a reader-produced `None` is still the only physical-EOF signal, and the task owns its logical stream end. There is no watcher, extra thread, extra store or outcome field.

## Observations (out of scope, not blockers)

- **Decoder state dropped on forced stop.** A trailing `\r` held by `IncrementalNewlineDecoder(translate=True)`, or a partial multibyte sequence, is lost when a reader is stopped without EOF (`:553`). This existed before the change and the delta does not make it worse.
- **stderr final after stdout final.** In the frozen loop, physical EOF markers are delivered stdout first. So a real stderr final, or a stderr seal following a real stdout final, can still land after the stdout final. Readers that consume only up to the last stdout final (`_read_outbox_task_result`) then leave that row behind. This existed before the change.
- **`duration` now includes delivery time** for ok, cancelled and limit outcomes, and that time depends on sink speed. This was already noted in the earlier review.
- **Constant name.** `SUBPROCESS_STREAM_DRAIN_TIMEOUT` now means a capture grace period. The docstring explains this; renaming it is optional.

</details>


### S2 probe closure

Corrected separate baseline and reset-control probes both exited 0, with all
owned child/Manager/harness cleanup checked. At recovery, baseline timeout
projection remains[0.0]; the process-local exact-owner clone with only the
consumed-retry reset reports[15.0], then[14.0] one controlled second later.
Both preserve the exact reserved row through proof grace, reserve/authorize a
real second request yielding `deadline-witness`, then commit the real failsafe
and acknowledge the original reservation. The reset does not clear a future
retry. Source files were unchanged. The prospective B veto is inferred from
these real deadline projections; no B gate was installed in either probe.

Preserved failed probes: initial combined invocation exited 143 during harness
cleanup after baseline evidence; a second reset setup had a wrong indentation
anchor and escaped before ownership cleanup, also143. Neither is acceptance.
The corrected probes register cleanup immediately after Manager construction
and explicitly retire the Manager before harness teardown. Evidence:
`b-child-deadline-probe.baseline.json`, `.reset.json`, and manifest in the
implementation evidence directory. A committed RED regression is being prepared.


### Capture correction final scoped closure

Claude Opus 5.5,Tier 1,72.83 seconds: PASS for CB1/CB2/CB3; no introduced defect.
All three owner questions answered yes. CB4 stays declined. Parent independently
parsed the isolated retry plugin's embedded source and diffed it against current
production: its only change wraps the data callback, retries it once after
OSError under suppression, then re-raises the original. Thus the reported old
keeper pass/newkeeper failure is attributable to the new zero-retry assertion.
The reviewer's inability to read the external plugin is a containment limit,
not unverified mutation semantics after this parent check. Full backend/platform
gates remain open; the runtime implementation review loop itself is closed.

<details>
<summary>Verbatim capture correction scoped closure review</summary>

**Verdict: PASS for CB1, CB2 and CB3, and none of them introduced a new defect.** I found nothing that calls for a reconsideration request.

I used read-only tools and edited nothing. I re-ran no tests, so the evidence below is the logs you supplied. The full SQLite/PG/platform CI is still pending.

## Results

| ID | Verdict | What I checked |
|---|---|---|
| CB1 | PASS | `_finish_streams` (`subprocess_runner.py:645-656`) no longer takes a `timeout` parameter. It reads `SUBPROCESS_STREAM_DRAIN_TIMEOUT` directly (`:663`). None of the five call sites (`:180`, `:220`, `:295`, `:338`, plus the definition) passes a timeout. No test or other module refers to the constant or the function. The behaviour is the same: one shared deadline, joins only for output adapters (`:664-666`), then stop/join, then `_raise_pipe_errors`, then the frozen delivery loop. One small side effect is that the constant is now read when the function runs, not when it is defined, so monkeypatching it now works. That is harmless and slightly better. |
| CB2 | PASS | `calls_after_failure` goes up on any callback call after the first raise, including `final=True` calls, and is checked to be 0 (`test_subprocess_runner.py:1666-1689`). The original identity check `caught.value is failure` is still there. In the runner, a callback exception leads to `except BaseException` and then `finally` (`:425-444`). `finally` stops and joins the adapters and closes the pipes, but delivers no more chunks. So the "never call a sink again after it raised" rule really holds, and the test asserts it. |
| CB3 | PASS | The added sentence (`05-Message_Flow_and_State.md:292-294`) matches the code. `consumer.py:479-482` seals stderr before stdout, and the `inherited` case of the test checks it (`test_consumer_terminal_events.py:302-308`). In that case both finals are missing, because the descendant holds the pipes. The clause says "when both logical finals are missing", which is exactly right. It does not overstate the order when stdout already got a real final, which is the known pre-existing case. |

## Is the test change what catches the mutant?

Yes. The control evidence shows it:
- **Old test against the retry mutant:** passes (`exit 0`). The original identity check alone does not catch the mutant, which confirms the gap CB2 described.
- **New test against the same mutant:** fails at `:1689` with `assert 1 == 0`. That line is the new assertion, and the line number matches the current file.
- **Sources unchanged by the controls:** the before and after hashes are equal (`6e279e…`, `fcdfdc…`) and match the current files. So the mutant was injected through the `-p` plugin and did not edit the sources.

Two gaps in the evidence, neither a blocker:
- **Mutant source:** the plugin `runtime_capture_cb2_retry_mutant` lives outside the repository, so I could only identify it by its hash (`f70e918b…`). I could not read it.
- **Green log:** it says 10 tests passed, but the command is not recorded, so I can't tell which 10.

## Observations (not blockers)

- The CB2 test does not prove the failure happens during frozen delivery rather than in the running loop. With 1 KiB reads, 16 chunks per pass and an early child exit, it almost certainly does. The new assertion holds in either phase, so this does not affect CB2.
- CB4 is declined as you directed. The repeated `outcome.status == "ok"` at `consumer.py:479-482` is unchanged.

## Answers to your three questions

1. **Is it correct?** Yes. The deltas only remove a parameter, strengthen a test and document an existing order. Physical EOF and the logical final remain separate, as do error precedence, hook and resource-cleanup ownership, and the 250 ms capture bound followed by unbounded delivery.
2. **Is it elegant?** Yes. CB1 removes a setting nobody used. CB2 adds one counter and one assertion. CB3 is one sentence that states an order the code already guarantees.
3. **Does it use the data structures properly?** Yes. Nothing new was added. The local queues are still the only transport, and a reader's `None` is still the only physical-EOF signal. The per-work closure flags still own the logical seal.

</details>


### S2 and B clarification review closure before implementation

Claude Opus 5.5, Tier 1, 388.96 seconds: PASS for the revised plan. All six
B-F1–B-F6 dispositions verified; all three owner questions answered yes. The
reviewer read the previously omitted runbooks directly this pass. R2-F1 accepted:
Source specs and strategy-A exact delta now name the normative MA-1.6a
clarification, with conditional proof-grace wording. R2-F2 accepted in the
admission acceptance matrix. R2-F3 accepted as predeclared distributions and
repeatability rules, separate from correctness assertions. R2-F4 accepted:
all three child deadline families must advance or retire when serviced; S2
repairs publication retry, leaving existing sentinel/grace owners intact.

Committed S2 regression RED: one failure in 1.85 seconds with 12 configured
workers. Exact assertion `timeouts and min(timeouts) > 0` saw `[0.0]`, after
real clean exit, both named broker failures, future-retry retention/no extra
I/O, and fresh-grace start had all passed. Cleanup completed without teardown
errors or surviving probe processes. Production reset authorized after this
plan PASS; the implementation and completed-work review are still pending.

<details>
<summary>Verbatim S2 and B clarification round-2 plan review</summary>

# Round-2 plan review: admission-plan arm B dispositions (B-F1 to B-F6) and S2 repair

**Verdict: PASS for the revised plan. Implementation isn't done yet: S2 is still unapplied and arm B is uncoded.**

- The source baseline is still `4c490043`. `_cleanup_children` at `manager.py:4533-4536` has no reset yet.
- All six accepted dispositions are reflected in the operative plan body, and S2 is a correct, minimal owner repair.
- I found four new P3 items. None blocks: each is wording or traceability, and none affects whether the plan can be implemented correctly or degrades the system.

**What I read.** This time I read the required surfaces directly, read-only:
- `docs/program-theory.md`: WT-2, WT-4, ALT-WT-001, ALT-WT-002 and ALT-WT-017.
- The agent-context `README.md`, `decision-hierarchy.md`, `principles.md`, `lessons.md` and the relevant parts of `engineering-principles.md`.
- Both required runbooks in full: `review-loops-and-agent-bootstrap.md` and `hardening-plans.md`.
- The Golden Rules and the 2026-10-06 test-clock lesson in `docs/lessons.md`.
- The repository has no module theory files.

I also re-read the source owners in `manager.py`, `base.py` and `multiqueue_watcher.py`, the operative plan files, and the [MA-1.6a] spec text in the working tree. I ran no tests or probes. The probe JSON is treated as supplied evidence.

## Checking each accepted disposition

| ID | Result | Evidence |
|---|---|---|
| B-F1 / S2 | **Verified** | **Reset placement.** The proposed reset goes after the survivors guard and the future-retry guard (`:4529-4534`) and before the grace check (`:4535`). Below are the cases where a due retry gets serviced:<br>• Proof read returns None, or proof is True: grace returns False (`:4457-4458`), then the envelope write. On success the child is retired; on failure `:4545` re-arms the retry.<br>• Proof is False and no grace has started: grace starts. The projection's retry branch (`:8301`) is skipped, so the grace deadline at `:8318-8320` becomes active (+15 s).<br>• Proof is False and grace started earlier: that grace has already expired, because the retry interval equals the grace interval. So the envelope is written immediately.<br>**Other paths.** A future retry is never cleared. The shutdown survivors path keeps its retry, which is correct because it wasn't serviced. The `deadline` break at `:4526` runs before the reset.<br>**Other child deadline families.** I checked these too, since B's predicate covers all of them. A proof-grace deadline always ends in retirement or a re-arm. A sentinel-recheck deadline advances `last_liveness_probe_ns` once it is due (`:4377-4384`). The only branch that doesn't advance needs either `is_alive()` True after the sentinel has fired, or `pid is None` on a started process. Neither is reachable in practice.<br>**Proposed test.** The regression being prepared at `tests/core/test_manager.py:10228` would kill both relevant mutants:<br>• Removing the reset fails at `:10316` and `:10318`.<br>• Moving the reset before the future guard makes the outage counters change, which fails at `:10307-10308`.<br>It uses Manager's `time` binding, not the stdlib. |
| B-F2 | Verified | Spec: "a ready child-sentinel observation or a due child sentinel/post-exit maintenance deadline". Plan: "A sentinel hint is not process-death evidence". This matches `_manager_child_timeouts`'s own docstring (`:8293`). |
| B-F3 | Verified | Both the plan and the spec say "Before each spawn delivery in the scope, including the first". |
| B-F4 | Verified | The only `_cleanup_children` callers are:<br>• `:5460`, the handler sweep that B suppresses<br>• `:7983`, convergence<br>• `:8381`, the outer turn sweep<br>• `:8670` and `:8684`, the shutdown drain<br>• `:4645` and `:4759`, termination<br>Termination requests are applied only at turn entry (`base.py:1438`), and convergence and drain run only at turn level (`:8382`, `:8418`, `:8366-8421`). So the narrowed sentence is exactly true. |
| B-F5 | Verified | The plan no longer calls the extra notification a no-op; it relies on existing owners. If the Event is set, the observer has already notified the strategy or is about to (`:547-549`), and that local hint persists until the post-wait consume (`multiqueue_watcher.py:1196`). If a deadline is due, `next_wait_timeout()` reaches 0 (`:8144`) and `_wait_for_activity_body` returns immediately (`multiqueue_watcher.py:1142`). Every yield cause is drained at the next turn's start (`:8326`, plus `drain()` clearing at `:467`), so no yield can repeat forever. |
| B-F6 | Verified | Yield-turn counts and yields per authorization are now metrics. Extra turns alone are not grounds for rejection. A material, reproducible latency regression means remove B or take the tradeoff to the owner. The 74% turn-span figure no longer appears as a cost claim. |

## New findings

| ID | Severity | Location | Evidence | Strongest counterargument | Suggested disposition |
|---|---|---|---|---|---|
| R2-F1 | P3 (traceability) | Repair plan: `Source specs:` (line 4), `## Proposed Spec Delta` (line 166 onward), S2 "Files" (lines 476-478). Spec `03-Manager_Architecture.md:342-345` (working tree). | The prepared S2 change edits **normative** [MA-1.6a] prose, but the S2 body says to edit only "[MA-1.6a]'s nearby implementation note". The plan's Source specs omit [MA-1.6a], and its Proposed Spec Delta has no [MA-1.6a] entry. That conflicts with decision-hierarchy's rule that normative edits carry an exact delta and a named strategy. Separately, "so a subsequent proof-grace interval becomes the active deadline" is accurate only when grace actually starts. When the proof read fails, or an earlier grace has already expired, cleanup publishes immediately. | The sentence is correct, small, under review now, and restores an existing contract. | Before S2 lands:<br>• Add [MA-1.6a] to Source specs.<br>• Add the exact sentence as a strategy-A insertion in the delta.<br>• Change the S2 Files wording to "normative clarification plus mapping".<br>• Optionally reword to "…so any proof-grace interval that then starts becomes the active deadline". |
| R2-F2 | P3 / nit | Admission plan, Acceptance Matrix "Ready maintenance" row (line 155): "before further authorization" | This is leftover wording from B-F3. It can be read as "after the first authorization". | One authorization per turn makes the intent fairly clear. | Use "before the turn's next spawn delivery, including the first". |
| R2-F3 | P3 | Admission plan decision rule: "meaningful reproducible latency regression" / "increases latency materially" | No threshold is set in advance, so an A/B outcome is open to post-hoc reading. Slice 1 asks to "predeclare comparison metrics" but not a decision criterion. | Escalating to the owner already bounds the risk. Tests must not hard-code machine-specific percentages anyway. | In Slice 1, name the distributions that decide the outcome (for example enqueue-to-start and completion-to-next-eligible-start), plus a reproducibility rule relative to A's observed spread across the alternating repetitions. This is a measurement criterion, not a test assertion. |
| R2-F4 | P3 / nit | Admission plan: "B's deadline predicate relies on `_cleanup_children` retiring each due retry it services" | The sentence is accurate for the defect, but B's predicate covers all three child deadline families. The other two were verified above to retire or advance when serviced. | Narrow and correct as written. | Optional: add "sentinel-recheck and proof-grace deadlines already advance or retire when serviced; S2 makes the retry deadline do the same." |

## The three owner questions

1. **Is it correct?** Yes. S2 restores [MA-1.6a]'s no-spin requirement. It doesn't shorten grace, acknowledge early, or touch custody or dispatch, and the reset sits at the only point where a due retry is serviced. Once S2 is in the common baseline, every child deadline B checks is cleared or advanced by the next ordinary sweep. So B's gate causes a bounded number of yields: at most one per sentinel event or per due deadline. It no longer causes a stall.
2. **Is it elegant?** Yes. It's a one-line reset in the owner, with no latch, timer snapshot or second policy in the projection. Making `_manager_child_timeouts` prefer grace over an expired retry was the alternative, and the plan rightly rejects it as a second policy owner. B stays a scoped flag plus one predicate that uses existing wakes.
3. **Does it use the data structures properly?** Yes. `terminal_retry_after_ns` now behaves as a classic one-shot timer: cleared when it fires and re-armed only when a publication fails again. Before, it was a fired timer left armed. The projection stays an O(tracked children) read with no new index, and the sentinel Event stays a hint rather than death evidence.

No RECONSIDERATION REQUESTED. Nothing here reverses ALT-WT-001, ALT-WT-002, ALT-WT-015, ALT-WT-017, ALT-WT-019 or any listed invariant.

## Observations (not actionable for this unit)

- **Frozen Manager clock in the prepared regression.** The test freezes Manager's `time_ns` (`test_manager.py:10256-10288`) while a real witness request dispatches. That also freezes `_expire_admission_retry_if_due` (`:2911`). If PostgreSQL pressure denies admission at standard concurrency, the retry would never come due, and `:10349` would fail. This is an untested hypothesis. If it happens, follow ALT-WT-017 rather than relaxing the test.
- **Pre-existing spin during shutdown drain.** An exited child kept for its live survivors (`:4529-4532`) keeps a sentinel-recheck or retry deadline at zero, so `next_wait_timeout` spins during the drain. S2 is right not to reset before that guard, and drain has no delivery scope.
- **Pre-existing fresh grace after a retry.** After a serviced retry, a fresh 15 s proof grace can start, so exit to failsafe can take about 30 s. S2 correctly leaves this alone.
- **Pre-existing stdlib clock patches.** Several existing tests patch `manager_mod.time.time_ns` on the shared stdlib module (for example `test_manager.py:10204`), which the 2026-10-06 lesson warns against.
- **Reset before the read.** If an unexpected, non-broker exception escaped between the reset and the re-arm, the retry would be dropped. That exception already aborts the turn, so this is negligible.

</details>


### S2 implementation and focused verification

The owner now clears `terminal_retry_after_ns` after the future-retry guard and
before proof grace. The existing failure path still re-arms it. No B gate,
scope flag, dispatch change or new timer store is included. Seven focused
SQLite cases passed in 2.23 seconds with 12 configured workers; the canonical
PG slice passed the same seven cases in 7.56 seconds without changing worker
policy or pressure settings. No PG pressure failure occurred in this focused
run. At that point full standard-load acceptance remained pending; the later
S2 completed-work record below records the completed local backend runs and the
still-open CI gate.

Parent inspected both isolated controls: removing the reset restores `[0.0]`
and fails the positive-timeout assertion; moving it before the future guard
renews the retry too early and fails the preserved-deadline assertion. Source
and test hashes were unchanged by controls. Targeted mypy, Ruff/format and
diff checks passed. One initial PG wrapper command rejected unsupported `-v`
before running tests; removing that flag produced the valid run. It was not a
product failure or passing test run. All owned test/probe processes were cleaned
up. Completed-work cross-family review and full backend/platform gates were
pending at this focused-check milestone; see the subsequent S2 review and gate
record below for their current status.

Capture CB1/CB2 exact GREEN command (evidence clarification, no rerun):
`./.venv/bin/python -m pytest tests/core/test_subprocess_runner.py::test_terminal_capture_delivers_backlog_after_capture_budget tests/core/test_subprocess_runner.py::test_late_captured_output_callback_failure_preserves_exception_and_cleanup tests/core/test_subprocess_runner.py::test_capture_join_reports_adapter_error_before_delivering_callbacks tests/core/test_subprocess_runner.py::test_root_completion_bounds_inherited_pipe_drain --tb=short`
ran after loading `.envrc`, with 12 configured workers, and returned 10 passed
in 3.28 seconds. The command is recorded here to close the review's evidence
traceability note; the original result and log remain unchanged.


### S2 completed-work cross-family review and dispositions (2026-10-28)

Independent Tier-1 reviewer: Claude Opus 5.5 (Anthropic), separate from the
OpenAI implementation author. Review completed in 395.43 seconds, exit 0,
verdict **no blocker**. Unit: the consumed-retry reset and its regression at
`784d6b7aad893c77bd69cf9323db0d0a185bda2c`, against source baseline
`4c49004320bb281a6557cd80072356dae225d047`. The review checked the actual owners
and classified its F1 pressure concern as an inference, not an observed failure.
It did not execute tests; the author and parent separately inspected the actual
RED/GREEN and isolated-control failures. This is completed-work approval for
S2, not approval of unimplemented admission B or closure of this whole plan.

Review artifact: `/tmp/weft-implementation-20261027/s2-implementation.result.json`.
Artifact SHA-256: fb3e1ead40803a309020ba2443be71d85fdc5f80aefa31ec55fceaf4299face5.
The complete review is retained verbatim so it does not depend on that temporary
path remaining available.

<details>
<summary>Verbatim independent S2 completed-work review</summary>

# S2 completed-work review: due-retry reset in `_cleanup_children`

**Verdict: no blocker.** The production change is correct, minimal and placed at the right owner. All four findings are in the new regression, and none is a defect in the reset itself.

**Unit and baseline.** The unit is the S2 delta at head `784d6b7a` against source baseline `4c490043`: `weft/core/manager.py::_cleanup_children` (lines 4521–4553) and `tests/core/test_manager.py::test_cleanup_children_retires_due_retry_before_clean_exit_proof_grace` (lines 10228–10413).

**What this review is based on.** I read files only and ran no tests, probes or git commands. Everything below is either "verified by reading" (current tree) or labeled as inference. I did not re-establish the supplied RED/GREEN/control logs; I only checked that they are internally consistent:
- `addopts` contains `-q`, so `-v` cancels it. That explains the non-verbose output in logs whose commands passed `-v`.
- The 7 items are 1 + 1 + 1 + 2 + 2, from two parametrized tests.
- The new test is collected as `shared`.

**Context I read, per the README read order:**
- `docs/program-theory.md`: WT-2, WT-4, and ALT-WT-002, -015, -017 and -019.
- `docs/agent-context/` `README.md`, `decision-hierarchy.md`, `principles.md` and `engineering-principles.md`.
- `review-loops-and-agent-bootstrap.md` in full.
- `testing-patterns.md`: Test Design Rules, Manual Reactor Test Drivers, and Patterns 10–12.
- `hardening-plans.md`: section headings only. This unit has no hardening trigger.
- `agent-context/lessons.md`.
- `docs/lessons.md`: the Golden Rules and the dated sections from 2026-09-14 through 2026-10-15. The lessons watermark is "(none)", so I did not reread the older dated sections.
- There are no module theory files (glob).
- The [MA-1.6a] spec text (lines 336–349), its implementation mapping (line 573), and the S2 records in the repair plan.

## Verification of the owner change (by reading)

| Check | Result | Evidence |
|---|---|---|
| Outer-deadline break comes before the reset | Yes | `manager.py:4529-4530` |
| Retained-survivor guard comes before the reset | Yes | `:4532-4535` |
| A future retry is never consumed | Yes | The `> time.time_ns()` guard at `:4536-4537` comes first, and the reset at `:4539` only runs when the retry is due or unarmed. |
| A serviced retry always ends in grace, retirement or a fresh re-arm | Yes | See the four cases below. |
| A failed publication re-arms a bounded retry | Yes | The unconditional re-arm at `:4549-4553` is reached after the reset on every failed write, including when a deadline-limited write returns False at `:4494-4495`. |
| No custody acknowledged early | Yes | The exact `delete(message_id=…)` at `:4578-4591` is reachable only after `committed` is True: task proof at `:4477-4478`, or a committed failsafe at `:4497-4499`. |
| No second policy owner | Yes | `_manager_child_timeouts` (`:8297-8326`) is unchanged. The test asserts the owner state (`terminal_retry_after_ns == 0`, test `:10327`), so a mutant that filtered expired retries in the projection would also fail. |

The four cases after the reset:
- **Proof read fails (None):** grace returns False (`:4457-4458`), then the envelope write.
- **Proof is True:** the envelope write returns True; the child is retired and the reservation acknowledged.
- **Proof is False with no grace started:** grace starts (`:4460-4462`). The grace deadline at `:8323-8325` becomes the only child deadline, because the sentinel branch requires `missing_since <= 0`.
- **Proof is False with grace started earlier:** a retry can only be armed after grace has returned False, so the earlier grace has already expired and the code publishes immediately. The one exception is a backwards wall-clock step, and even then the active deadline is positive.

The reset therefore cannot consume future or unserviced work. It turns `terminal_retry_after_ns` into a one-shot timer: cleared when serviced, re-armed only by a new failure.

## Verification of the regression (by reading)

- **Real dependency faults.** The test patches the backend class's `peek_generator` and `write`, filtered to the exact `ctrl_out` name. The proof read fails through the real `except` clause at `:4437`; the write fails through the real retry loop at `:4493-4507`. No owner result is replaced with a constant.
- **Owner-local clock.** Only `manager_mod.time` is replaced, as the 2026-10-06 lesson recommends. A grep of `manager.py` shows it only calls `time_ns`, `monotonic`, `perf_counter` and `sleep`, so the facade is complete. Real time is restored before `stop`/`cleanup`. Fixture teardown order (monkeypatch undone before `manager_setup`) is also safe.
- **Exact reserved row.** Retention is checked by `exact_timestamp` at `:10311`, `:10329` and `:10382`, and acknowledgement at `:10392`.
- **Sensitivity.**
  - Removing the reset fails at `:10325`.
  - Moving the reset before the future guard fails at `:10316`.
  - Moving it after the grace check would fail at `:10325`/`:10327`.
  - Deleting the re-arm fails at `:10310`.
  - Filtering in the projection fails at `:10327`.
- **Cleanup ordering in `finally`.** The test's own `stop()`/`cleanup()` duplicates the fixture, but the ordering is justified: the Manager's sentinel adapter must release the process sentinel before `process.close()`. The supplied RED and mutant logs show no teardown error.

## Findings

| ID | Severity | Location | Finding and evidence | Strongest counterargument | Suggested smallest disposition |
|---|---|---|---|---|---|
| S2-F1 | P2 (test robustness under the declared full-load gate; inference, not observed) | `test_manager.py:10357-10370`, with `manager.py:2219-2231`, `:2829-2835`, `:2906-2913`, `:2941` | The witness phase requires the second request to dispatch within one `process_once()`. On PostgreSQL, admission measures **global** server usage (`usage["total"]`). One denial leaves the inbox row in place, so `:10358` fails immediately. Even a relaxed oracle could not recover, for three reasons: the denial sets `_admission_retry_after_ns` from the frozen `time_ns`, so `_expire_admission_retry_if_due` never fires; `_admission_allows_queue` short-circuits on the blocked lane (`:2941`); and nothing in the witness phase calls `_clear_admission_retry` (no reap, launch or resident change). The test does not own its PG headroom, which conflicts with the 2026-10-15 lesson (a progress proof must fit headroom it owns). | The focused PG run passed under the normal worker policy. The window is about one turn. A denial needs near-ceiling server usage, which the suite may not reach. The approved S2 task explicitly required this witness. | **OWNER DECISION.** Either move the witness to B's slice (see F2), or keep it. If kept and the parent's full-load PG run fails at `:10358`/`:10364`, apply the TS-0 stop condition: the oracle assumed headroom it does not own. That is not a product defect and not grounds for reducing workers. |
| S2-F2 | P3 (unnecessary work for this unit) | `test_manager.py:10263-10264`, `:10337-10383` | The witness phase cannot detect any S2 regression. Both controls fail before it, and the probe table shows the **unrepaired** owner also dispatched in one turn. It costs about 50 lines, a real spawned task, and the F1 exposure. Its real value is as B's future discriminator, where B's deadline veto could stall dispatch. | The approved task required "normal second request dispatch". The custody assertions at `:10381-10383` do add that ordinary reactor turns (not only direct calls) preserve grace. | **OWNER DECISION.** Prefer moving the witness to B's acceptance test, where it discriminates. Otherwise keep it with a one-line comment saying it guards B, not S2. |
| S2-F3 | nit | `test_manager.py:10324-10326` | `min > 0` / `max <= GRACE` is looser than the state it checks. With `retry == 0` and `grace_started == now` asserted, the projection is exactly `[MANAGER_CHILD_TERMINAL_PROOF_GRACE_SECONDS]`. Asserting that equality would state the [MA-1.6a] rule "proof-grace becomes the active deadline" directly. | It matches the existing bounds style at `test_manager_burst_bookkeeping.py:148-149`, and `:10322-10327` already pin the result together. | Optional. No change needed. |
| S2-F4 | nit | `test_manager.py:10306-10335` against spec line 344 | The new spec clause "another failed publication installs a fresh retry" is not fired *after* a serviced retry. Only the initial failure's re-arm is asserted (`:10309-10310`). | It is the same unconditional line `:4549-4553`, so no realistic mutant separates the two paths. The early-reset control log incidentally shows a fresh re-arm. | Optional. Treat `:10309-10310` as the equivalence owner. |

## Answers

1. **Is it correct?** Yes. The reset runs only after exit is confirmed, after the outer-deadline and survivor guards, and after the future-retry guard. Every path after it ends in a positive grace deadline, retirement after task proof or a committed failsafe, or a fresh bounded retry. Custody, grace length, proof classification and dispatch are unchanged. It restores [MA-1.6a]'s rule against spinning on an expired deadline without touching MF-5/MF-6, MANAGER.11/.18 or IMPL.8/.10. It adds no local-to-global authority (ALT-WT-015), no polling cap (ALT-WT-002) and no queue retention (ALT-WT-019).
2. **Is it elegant enough for an expert Python/systems programmer?** The production change, yes: one assignment at the single point where the retry is serviced, with an accurate one-line comment and a `Spec:` docstring in the house style. Rejecting a snapshot, latch or projection filter was right. The test is coherent and falsifiable through the post-recovery assertions. The witness phase (F2) is the part an expert would question as S2 evidence.
3. **Does it use the underlying data structures and primitives properly?** Yes. It reuses the existing `ManagedChild` convention that a zero ns field means "unarmed", the existing projection, and the existing re-arm. It adds no new state, index or wake mechanism. The test uses real spawn processes, real broker queues with dependency faults, exact-ID reservation checks, the owner-local clock pattern, and a causal file-based ready/release fence.

## Observations (not actionable for this unit)

- **Wall-clock child deadlines (pre-existing).** Retry and grace use `time.time_ns()`, so an NTP step stretches or collapses them. Monotonic time is the classical choice, but that is outside S2.
- **Exception escaping after the reset.** If an exception outside `(BrokerError, OSError, RuntimeError)` escaped between the reset and the re-arm, the retry would already be cleared. The fallback is then a past-due sentinel recheck, the same zero timeout as before S2. It is no worse than before, and such an exception already aborts the turn.
- **Frozen clock freezes other Manager timers.** During the witness phase, stale-launch recovery, leadership, heartbeat and admission timers are frozen as well. `drive_until`'s 20 ms wait slice keeps the loop live; the effect only matters on failure paths.
- **Process-wide broker patch.** The class-level patch applies to every thread in the xdist worker, but the exact queue-name filter keeps unrelated writes (including the witness's) unaffected.

**Scoped verdict: no blocker.** This review does not close the unrun full-suite SQLite/PostgreSQL or platform gates. That full-load PostgreSQL run is the evidence that decides whether F1 actually fires.

</details>

#### S2 finding dispositions

| Finding | Disposition | Rationale, evidence, and required action |
|---|---|---|
| S2-F1 | Accept as a residual test risk; retain the witness for imminent B, no source/test change in this record | The witness assumes one-turn PG admission without owning global server headroom, and its frozen Manager `time_ns` would prevent a denied wall-ns admission retry from expiring. The focused PG run and subsequent full standard-load PG run passed; that establishes non-occurrence in those runs, not that the hypothesis is false. Preserve the normal concurrency and pressure settings. If the witness fails at its source-retention or launch assertion, stop acceptance/corrective reruns under [TS-0]/[ALT-WT-017], preserve the original load and admission state, and distinguish legitimate pressure denial from a product or clock-fixture defect before changing the oracle. Do not reduce workers, bypass admission, invent capacity, or enlarge a timeout to obtain green. This CI investigation is not pre-decided by the hypothesis. |
| S2-F2 | Accept the coverage distinction; deliberately retain the witness for B | The unrepaired owner dispatched the second request in the disposable baseline, and both S2 mutation controls fail before the witness. Therefore the witness is not credited with sensitivity to the S2 reset. It is a retained first-dispatch/retained-custody baseline for B's upcoming child-deadline gate, where an overbroad gate can newly withhold eligible work. The intervening real reactor turns also check that proof grace and exact custody survive ordinary dispatch. At the B slice, explicitly carry this keeper into the B acceptance mapping and prove its B-specific discriminator; do not claim that unimplemented B has already been verified. No test move or comment edit is authorized by this documentation-only record. |
| S2-F3 | Decline optional exact-list assertion | Positive bounded timeout, exact grace-start time, and zero consumed retry together express the relevant owner-state contract; both intended reset controls fire. Replacing these with exact singleton-list equality adds representation coupling without repairing a demonstrated coverage gap. Keep the existing assertions. |
| S2-F4 | Accept the equivalence-owner analysis; no additional case now | Initial publication failure exercises the same unconditional re-arm assignment that follows every failed publication after the reset. The early-reset mutation also produced a second real failed publication and fresh retry, but that incidental control evidence is not mislabeled as a dedicated committed post-recovery test. No separate production branch distinguishes the two re-arms. If that owner is later split or gains conditional behavior, add the corresponding distinct fault control then. |

The witness retention is an explicit scope decision with a stated cost (one real
spawn and an unowned-headroom assumption). It is not a claim that passing full
suites removes that cost or uncertainty. No production or test changes were made
for these dispositions.

#### Distinct author fresh-eyes S2 review

Reviewer: the OpenAI S2 implementation author, re-reading the committed owner,
proof/grace helpers, deadline projection, acknowledgement path and regression
after the independent review. This is an author fresh-eyes pass, not a second
independent or cross-family approval. Scope and baseline are the same S2 delta
above; verdict **no production blocker**, with S2-F1/F2 retained as explicitly
dispositioned test limits.

1. **Is it correct?** Yes. `_cleanup_children` first respects its absolute
   cleanup deadline, confirms exit, and retains any still-live managed survivors.
   Its future-retry guard precedes the reset, so unserviced/future work remains
   armed. Clearing the consumed retry before the grace helper prevents a fresh
   grace interval from being hidden by an expired retry in
   `_manager_child_timeouts`. Unknown proof still follows the existing failsafe
   attempt; an unsuccessful write re-arms the bounded retry. Task-owned proof or
   a committed failsafe is still required before exact reserved acknowledgement.
   The real dependency-fault RED failed on `[0.0]`; removing the reset reproduces
   it, while moving the reset earlier fails the preserved-future-deadline
   assertion. The fix restores [MA-1.6a] without changing proof classification,
   grace length, admission, or dispatch. F1 concerns the witness oracle, not a
   defect demonstrated in this owner repair.
2. **Is it elegant enough for an expert Python and systems programmer?** The
   production repair is direct: consume a one-shot timer at its existing service
   point and let the existing failure path re-arm it. A snapshot, latch, or
   timeout-projection filter would duplicate policy and leave stale owner state.
   The reason comment and method-level spec reference explain the boundary
   without adding a helper. The test is longer than the minimum S2 owner proof;
   its extra witness is retained deliberately for imminent B, not defended as
   S2 mutation coverage. That distinction and its headroom risk remain visible.
3. **Does it use the underlying data structures and primitives properly?** Yes.
   It reuses `ManagedChild.terminal_retry_after_ns == 0` as the existing unarmed
   state, the unchanged child-timeout projection, the existing sentinel owner,
   and exact-ID queue custody. It introduces no index, cache, queue, worker,
   polling cap, or second scheduler. The projection remains an O(tracked
   children) read; the reset is local owner bookkeeping. The real broker faults
   and ready/release file fence verify dependency and execution boundaries
   without making process hints authoritative lifecycle facts.

No standing invariant or refused alternative is reversed, so no
`RECONSIDERATION REQUESTED` applies. This fresh-eyes conclusion does not promote
B's scope flag, pre-fetch gate, receipt policy or batching.

#### Current acceptance status after S2

At head `784d6b7a`, the completed local standard-load SQLite run reported
**5,970 passed, 49 skipped in 196.44 seconds**
(`/tmp/weft-implementation-20261027/repair-v2-sqlite.log`). The completed local
standard-load PostgreSQL run reported **5,921 passed, 27 skipped in 388.33
seconds** (`/tmp/weft-implementation-20261027/repair-v2-pg.log`). These runs
include S2 and preserved the configured concurrency. Neither observed the
S2-F1 pressure/frozen-clock failure. This is bounded run evidence, not proof
that every future global-pressure schedule satisfies the witness's assumption.

The parent verified local final mypy (478 files), Ruff, formatting (506 files),
suppression-registry checks, and six documentation tests (1.10 seconds) passed
before this review-record update. This update itself has a clean `git diff --check`; it does
not claim those earlier documentation tests ran against the newly appended
record.

CI run `40715396491` completed with failure. All Windows/POSIX core groups,
including all Windows Python 3.14 groups, passed. The PostgreSQL remaining shard
failed `tests/tasks/test_runner.py::test_command_session_terminate_kills_descendants`
while waiting for `interactive-child.pid`; its log is
`/tmp/weft-implementation-20261027/ci-v2-pg-remaining.log`. That is a different
test from S2, and its cause remains under investigation. This record does not
label it flaky or establish a causal relationship to the changes. All four
dependent extension/integration jobs were skipped, not passed. The parent
successfully dispatched the same-head failed-job rerun with
`gh run rerun 40715396491 --failed`; its result is outstanding at this record.
Keep this plan **draft** and keep completion, merge, and dependent admission
implementation/measurement gated on the parent's CI investigation and remaining
required verification. Do not mark the plan index completed based on local
full-suite passes alone, a rerun request, or skipped dependent jobs.


### T5 fixture repair evidence and author fresh-eyes pass (2026-10-28)

The independent review is running; this record does not claim its approval.
Parent verified the exact diff, actual control driver and four-arm results in
`/tmp/weft-implementation-20261027/t5-review-evidence.md`. The old fixture leaves
the live root and descendant and three reader/exit threads after the injected
readiness assertion. The candidate preserves that exact exception object, adds
status/stderr context, and leaves no live owned process or thread. A no-op
`CommandSession.terminate` still fails exactly at
`assert _wait_for_pid_exit(child_pid)` after the existing exit guard; independent
cleanup then completes. The probe compiled old AST definitions using the
candidate filename, so the old traceback's filename is a label limitation, not
evidence that it ran the candidate. Parent inspected the source selection.

Both generated-script consumers passed on SQLite (2 passed in 4.01 s) and the
canonical PostgreSQL wrapper (2 passed in 5.33 s), with normal worker settings.
Targeted mypy, Ruff and formatting passed; all-extras environment restored.
Exact commands and logs are retained in the evidence artifact. Final file SHA256:
`401b5376991f80f2a0e664f8c6bd0b25c54571af4bada7d94f0cbe6e5b6c9012`.

Distinct parent fresh-eyes assessment after implementation: the fixture repair
is correct for these generated programs. Cleanup ownership now starts immediately
after session acquisition, the failure remains visible, and cleanup cannot make
the earlier termination assertion pass. It is simpler to remove the unused
imports than to grant startup more time. The existing identity-verified tree
primitive plus a pinned descendant covers root-alive readiness failure and a
broken termination that leaves a descendant after the root exits. No new helper,
production seam or timer is introduced. This is appropriate use of existing
process identity primitives. The cleanup helper signals and waits boundedly; it
is not a general proof against arbitrary OS refusal to kill. The actual controls
verified no remaining owned processes or threads. No product runtime defect or
CI timeout cause is claimed by these fixture changes.

T5 exact focused commands, after `. ./.envrc`:

```bash
./.venv/bin/python -m pytest tests/tasks/test_runner.py::test_command_session_terminate_kills_descendants tests/tasks/test_runner.py::test_task_runner_timeout_terminates_command_descendants --tb=short
./.venv/bin/python bin/pytest-pg --all tests/tasks/test_runner.py::test_command_session_terminate_kills_descendants tests/tasks/test_runner.py::test_task_runner_timeout_terminates_command_descendants
./.venv/bin/mypy tests/tasks/test_runner.py --config-file pyproject.toml
./.venv/bin/ruff check tests/tasks/test_runner.py
./.venv/bin/ruff format --check tests/tasks/test_runner.py
```

The original CI test used five configured workers on Ubuntu/Python 3.14.8;
local gates used twelve configured workers on macOS/Python 3.14.9. Neither was
overridden. The interactive `start_session` path does not call the repaired
one-shot subprocess supervisor; its implementation and this test were unchanged
from the repair baseline. That narrows the path but does not prove the CI cause.


### Runtime head CI gate, attempt 2 (2026-10-28)

Actions run `40715396491`, attempt 2, completed **success** at exact head
`784d6b7aad893c77bd69cf9323db0d0a185bda2c`. All core groups, lint, coverage,
Django integration, Docker, macOS sandbox and Microsandbox jobs report success.
The four dependent jobs ran and passed; they are not the skipped jobs from
attempt 1. Verified result is retained in
`/tmp/weft-implementation-20261027/ci-v2-attempt2-final.json`.

The same-head pass demonstrates that the original PostgreSQL readiness failure
is intermittent; it does not establish its cause. The original Windows 3.14
first-autostart failure at the earlier repair head likewise remains causally
unresolved. This run verifies the runtime/S2 head, not the subsequent T5
fixture-only working-tree delta. T5 review and new-head CI remain pending; the
plan remains draft. No merge and no admission production change occurred.


### T5 independent completed-work review (2026-10-28)

Tier-1 Claude Opus 5.5 completed in 250.59 s, exit 0, **no blocker**; all three
owner questions answered yes. Parent dispositions: F1 accepted as provenance
clarification. The probed candidate differs from the integrated file only by
Ruff's wrapping of the pinned-child cleanup call; parent independently compared
full-module ASTs and verified equality and the final SHA recorded above. Focused
backend greens ran after formatting. F2 left unchanged: the vestigial importorskip
predates this delta, and removing one instance adds unrelated churn. No code
change or control rerun is needed for either finding. Broader observations remain
explicit below; no new runtime repair is implied.

<details>
<summary>Verbatim T5 cross-family review</summary>

**Verdict: no blocker.** The T5 delta is correct, idiomatic, and as small as it can be. I have two low-severity findings and neither needs a code change.

This review was read-only (Class 0). I didn't run any tests, the probe, mypy or ruff. Every claim marked "executed" below comes from the evidence you supplied, not from a rerun of mine.

**Surfaces consulted:** `docs/program-theory.md` ([WT-2], [WT-4] §4.2–4.4, [ALT-WT-015], [ALT-WT-017], [WT-7]), `docs/agent-context/` README, decision-hierarchy, principles and engineering-principles, testing-patterns (rules 1–10, Patterns 1, 10, 12), review-loops (completed-work and scoped-change prompts), `docs/lessons.md` (Golden Rules plus the dated sections from 2026-09-11 on; the coalescing watermark is "(none)"), `08-Testing_Strategy.md` [TS-0], and the plan's Task 4 T5 text (`docs/plans/2026-10-27-runtime-and-test-repair-plan.md:550-572`).

## Code paths I checked

- **`start_session`** (`weft/core/runners/host.py:1046-1144`): starts a `Popen` with inherited `os.environ` and three pipes. Three daemon threads are attached: two readers and a `process.wait()` thread. The handle comes from `_host_handle(pid)` (`host.py:111-124`), which records the root's `create_time` right after `Popen`. That gives the cleanup a root identity fixed at birth, not one reconstructed later.
- **`CommandSession.terminate`** (`weft/core/tasks/sessions.py:141-176`): uses the unverified `terminate_process_tree(pid)` and then waits on `Popen`. The cleanup never calls it.
- **`RunnerHandle.scoped_host_processes`** (`weft/ext.py:125-151`): returns `(pid, create_time)` pairs. It falls back to `(pid, None)`, which the verified primitive refuses.
- **`terminate_verified_process_tree`** (`weft/helpers/__init__.py:425-461`, verified by `_verified_host_process` at `:402-422`):
  - It refuses a missing or non-finite identity, a zombie, or a `create_time` mismatch beyond 1 ms.
  - It acquires descendants from the verified root instance and observes death without `waitpid`, so it doesn't steal the reap from `Popen`.
  - It catches every `psutil.Error`. Its own contract tests are at `tests/tasks/test_runner.py:148-250`.
- **Both callers of the generated scripts**:
  - `test_task_runner_timeout_terminates_command_descendants` (`test_runner.py:2056`) and `test_command_session_terminate_kills_descendants` (`:2552`).
  - Neither script uses `record_and_return`, `Iterator` or `Mapping`. The module-level imports at `:13` and `:25` are still used elsewhere (14 occurrences), and no other generated string in the file carries the injected import.

## Your specific questions

| Question | Answer and evidence |
|---|---|
| Is cleanup independent of the termination oracle? | **Yes.** `finally` runs only after `assert _wait_for_pid_exit(child_pid)` has been evaluated, so the cleanup can't make the oracle pass. The cleanup uses the verified primitive, never `session.terminate`. (Executed, supplied) The no-op-terminate arm fails at `:2585` after about 5.1 s, then cleans up fully. |
| Does it avoid acting on a reused PID? | **Yes.** The root identity comes from `_host_handle` at spawn. The child identity is pinned while the child is provably alive: it wrote its pidfile and then sleeps for 30 s. Both go through the `create_time` match. In the normal path the root is already reaped or a zombie, and the child is dead, so both calls return `False` without sending a signal. |
| Is the original failure preserved? | **Yes.** `add_note` plus a bare `raise` keeps the same exception object (the repo already uses `add_note` in 5 test files; `requires-python>=3.12`). The cleanup calls catch `psutil.Error` internally, and `stop_monitor` catches too, so in practice the cleanup can't replace the original exception. (Executed, supplied) `is_readiness_marker: true` holds in both readiness arms. |
| Any leak on readiness failure? | **No.** Before the fix, a readiness failure skipped cleanup entirely. Now the handle is captured at `start_session` and the tree kill covers the live root and its child. (Executed, supplied) The old arm left the root, the child and 3 threads behind; the candidate arm left nothing. The reader threads exit because the child, which inherited the pipes, is killed too. |
| Does a no-op terminate still fail before cleanup? | **Yes**, as shown above. Root-tree cleanup comes first and the pinned child second. That order is what catches the real bug this test exists for: terminate kills the root but orphans the child, which the tree walk can no longer see. |
| What if the root exits before readiness? | The tree call is a safe no-op, because a zombie or `NoSuchProcess` gives `None`. `returncode()` reports correctly: the `wait` thread reaps the root as soon as it exits, and before that `poll()` returns `None` because the `waitpid` lock is held. stderr includes the child's stderr too, through the inherited pipe. One gap remains: a root that dies after spawning the child but before the pidfile appears would leave an orphan that isn't covered. These scripts can't do that without an external kill, so I'm not asking for a change. |
| What if cleanup itself fails? | The primitive swallows `psutil.Error` and returns a boolean. Because the test ignores the return value, a refused identity only produces a warning. That is right for cleanup that must not mask the subject's failure. |
| Are timeouts and concurrency unchanged? | **Yes.** 2 s readiness, 5 s exit guard, no retry, no change to the worker count. The only extra waiting is the primitive's default 0.5 s, and only when something survives. The old 0.1 s probe and bare `.kill()` are gone. |

**Is it correct?** Yes.

**Is it elegant, expert systems programming?** Yes. It reuses the identity-verified primitive and the handle's own scoped identities. Readiness moves inside the try that owns the session, the failure is annotated in place, and there's no new abstraction, fixture API or production seam.

**Proper data structures and primitives, without overcomplication?** Yes. `tuple[int, float] | None` matches the primitive's `(pid, create_time)` signature and the shape `scoped_host_processes()` returns. A `psutil.Process` object would have been worse, because the primitive deliberately re-verifies from the raw pair. The `handle is not None` guard is required by the `CommandSessionProtocol` type, not defensive clutter.

## Findings

| ID | Severity | Location | Finding and evidence | Strongest counterargument | Smallest disposition |
|---|---|---|---|---|---|
| F1 | P3 (evidence provenance) | Probe report `candidate_sha256 c94cf6c9…` vs the final file | The probe certified a candidate that is not byte-identical to the final tree. The driver's `new_tail` splits the call at `:2594` over three lines; the final file has it on one 86-character line (`line-length = 88`), consistent with `ruff format`. I compared the driver text with `test_runner.py:2572-2595` and `:1688-1739` by reading them, and found no other difference. The two hashes aren't the same object (one is the probed candidate, the other the final file), and I couldn't hash the file myself. | Formatter-only reflow can't change what a single call does. The focused SQLite/PG greens were presumably run on the formatted file. | Add one line to the T5 evidence: "probed candidate differs from final only by ruff-format wrap at `:2594`". No rerun needed. |
| F2 | nit | `test_runner.py:2553` | `pytest.importorskip("psutil")` is vestigial: `psutil` is imported unconditionally at `:21`, and the diff now uses `psutil.Process` directly at `:2583`. Before the diff the inner rebinding at least made it look intentional. | It was already vestigial before the diff. The sibling at `:2057` and other tests share the pattern, so deleting one is incidental churn (Golden Rule 12). | Leave it (my recommendation), or delete it in a separate hygiene pass. |

## Observations (not actionable this pass; owner decision)

- **O1, sibling caller** (`:2077-2088`): it still kills a bare PID with `psutil.Process(child_pid).kill()` and no identity. Its readiness wait sits outside the try, so a readiness failure after `run()` would skip cleanup. That's the same pattern T5 just fixed for the session test. `run()` owns the root's lifetime there, so only a child orphaned by a real `run()` bug could leak. It's pre-existing and this diff doesn't make it worse; aligning it would be a scope expansion.
- **O2, oracle uses a bare PID:** `_wait_for_pid_exit(child_pid)` could pass or fail wrongly if the PID were reused within 5 s. The pinned identity could tighten it, but the frame keeps the oracle unchanged, and the risk is negligible.
- **O3, pipe and stdin file objects** on `CommandSession` are never closed in either path, which can raise `ResourceWarning` at GC. That's production session ownership and pre-existing; I'm not asking for a session IO redesign.
- **O4, theoretical partial pidfile read:** `_wait_for_pidfile` could in theory read a partial write and pin the wrong PID. A small single write to a regular file makes this effectively atomic, the empty-read case is already handled, and the old code would have killed the same wrong PID with no verification. So it's pre-existing and not worse.
- **O5, CI cause still unresolved, as you accepted:** one possibility consistent with the old missing evidence is that the generated parent failed its `tests.helpers.typing` import under a different `sys.path` and crashed before spawning. The new note (returncode plus stderr, which includes the child's stderr) would now show this. This is my inference, not a diagnosis, and a green rerun must not be called causal.
- **O6, probe quirks, cosmetic:** the old arm's process list repeats child 39246 (once from the injected wait, once from the root walk). Its traceback cites the candidate filename while running the original AST; line 2581 matches the original source offset (the candidate line plus the 8 removed import lines), which supports your provenance caveat.
- **O7:** the note covers only the readiness `AssertionError`. Any other exception still gets cleaned up, just without the extra context. That's adequate.

No `RECONSIDERATION REQUESTED`. The diff leaves TS-0, WT-2/WT-4, process-identity verification and queue/state/custody contracts unchanged.

**Scoped verdict: no blocker.** F1 and F2 are non-blocking. As you noted, the pending same-head CI rerun doesn't certify this uncommitted delta; a new head has to run after this review.

</details>


### Fixture-head Windows failure (2026-10-28; investigation open)

Run `40721270954` at head `40f094c34e3a598ee8c778f5bda53be72aea54aa`, Windows
3.12 remaining job `122932890976`, failed the KEEP parameter of the R3 Consumer
regression: expected `BaseResourceMonitor subclass`, observed PermissionError
during access to the test's `command.pid` marker. The job reported 1 failed, 2167 passed and
75 skipped. Log: `/tmp/weft-implementation-20261027/ci-v3-windows312-remaining.log`.
The other jobs are not all finished. This is a new actionable test-boundary
failure, not a passing runtime gate and not evidence for weakening the expected
error. T6 above records the proposed removal of unnecessary readiness I/O.

Parent confirmed the CI payload alone does not distinguish `exists()` from
`read_text()` or identify a sharing/permission cause. A controlled read fault
reproduces the same masked validation assertion in both reserved-policy cases;
it is a dependency-sensitivity proof, not a Windows syscall diagnosis.


T6 parent refinement: the first handoff candidate passed cross-family source
review (275.35 s, no blocker; required context read). Its F1 identified observation
before cleanup registration, suggesting rejection as practically unlikely. Parent
accepts the simpler alternative: retain the already-owned `Popen` immediately
instead of reconstructing a psutil identity. An isolated kill-plus-stdout-EOF
without reap control showed that `poll()` in the assertion falsely passes both
policies. The recorded-returncode assertion fails both missing rollback and
kill-without-reap, while normal/marker-fault cases pass and independent cleanup
needs no rescue. The final diagnostic will describe failure to reap, not assume
the child is still live. Parent inspected exact assertion failures and control
source. This test establishes acquired-root rollback; it makes no claim about
Windows venv redirector descendant coverage. Final-refinement review and Windows
CI remain pending.


### T6 first cross-family review and Popen refinement evidence

Claude completed the first T6 review in 275.35 seconds, with required context read and no blocker. F1 is accepted through removal: retain the acquired Popen before assertions rather than reconstructing process identity. O1 is an accepted proof limit: acquired root only, not Windows redirector descendants. O2 is eliminated with the same simplification. Broader observations remain outside this test-only slice.

<details><summary>Verbatim first T6 review</summary>

# T6 review: `test_monitor_validation_failure_finalizes_consumer_and_reaps_command`

**Verdict: no blocker.** One nit (F1), recommended for rejection. No `RECONSIDERATION REQUESTED`. Windows CI is still required before T6 can be called complete.

## Context completion

I read the mandatory context in the required order:
- `docs/program-theory.md`.
- `docs/agent-context/README.md`, `decision-hierarchy.md`, `principles.md` and `engineering-principles.md`.
- The runbooks `testing-patterns.md` and `review-loops-and-agent-bootstrap.md`.
- `docs/agent-context/lessons.md`.
- `docs/lessons.md`. The watermark in `docs/coalescing.md:91` is "(none)", so I read the Golden Rules and every dated section, through the working-tree 2026-10-28 entry.

Governing contracts: `06-Resource_Management.md` [RM-5.1] (lines 123–175), `08-Testing_Strategy.md` [TS-0], and plan Task 4 T6 (`2026-10-27-runtime-and-test-repair-plan.md:576-592`). The only "theory" files are the program theory and two plans; no module theory exists for runners or Consumer, which I'm recording rather than skipping. I read no other runbooks; they don't apply to a test-only delta. I ran nothing. Everything below comes from reading the code, except where I attribute a result to your supplied evidence.

## Verification of the requested points (my reading)

- **Host call site and binding.**
  - `host.py:46-49` imports `run_monitored_subprocess` into Host's own namespace. The only call is the bare-name, keyword-only call at `host.py:977-994`, so patching `host_module.run_monitored_subprocess` takes effect.
  - The process is created first: real `subprocess.Popen` at `:936`, then `_host_handle` at `:967`, then the handoff.
  - Neither global `subprocess.Popen` nor the validator is patched.
  - The wrapper's `**kwargs` exactly matches today's keyword-only call. If the call ever passes positional arguments, the test fails loudly.
- **Supervisor ordering.**
  - Inside `subprocess_runner.py:107`, the order is: worker-start and handle callbacks (`:108-117`), then `load_resource_monitor` (`:119-124`), then `TypeError` at `resource_monitor.py:273-276`.
  - The `except BaseException` at `:425-431` then calls `_cleanup_after_execution_failure` (`:447-471`): tree stop, then `wait`, then `kill`/`wait` if still alive. The original error is re-raised.
  - The wrapper only adds steps before this function; nothing in it changes.
- **Identity pin.**
  - `poll() is None` on an unreaped `Popen` means the PID can't have been reused when `psutil.Process(pid)` runs. On POSIX the unreaped zombie holds the PID; on Windows the open process handle does.
  - psutil 7 records `(pid, create_time)` when the object is constructed (`psutil/__init__.py:346-347`). After rollback reaps the process, `is_running()` therefore can't be fooled by PID reuse.
  - This pin is sounder than the old one, which relied on a PID the child wrote to a file.
- **Consumer propagation.**
  - The runner runs on an in-process worker lane, so the patch applies.
  - Exceptions are caught at `consumer.py:376-386` and finalized at `:528-534`, which produces `failed`, `state.error`, the reserved policy and `work_failed`.
  - `cleanup_on_exit=False` (`:1196-1206`) leaves runner rollback as the only thing that kills the child.
  - If the wrapper's own assertion fires, its message lands in `state.error` and shows up in the first assertion's output, so failures are easy to diagnose.
- **Kept assertions.** Lines 174–183 still check: the real validation error, exactly one `work_failed`, `CLOSED`, KEEP/CLEAR reserved custody, a recorded child, and a dead child.
- **Unchanged guards.** The 10 s `drive_until` guard and the worker count are unchanged. The only wait removed is the 5 s marker poll; none was added.
- **Removed imports.** `threading`, `Path`, `tmp_path` and `subprocess_runner` have no remaining uses in the file.
- **Test count.** 3 + 1 + 2 + 4 = 10, which matches your "10 passed".

## Findings

| ID | Severity | Location | Finding | Strongest counterargument | Suggested disposition |
|---|---|---|---|---|---|
| F1 | nit | `tests/tasks/test_consumer_terminal_events.py:152-157` | The wrapper now runs *before* the supervisor's rollback scope. In the old design the hook ran inside it (`subprocess_runner.py:107`/`:425`). If the wrapper raises after it has the live `Popen` but before `children.append` and the forward, nothing owns the child: not production rollback, and not the test's `finally`. That breaks the 2026-10-28 lesson "ownership starts at acquisition". | In practice this can't happen. `poll()` on your own unreaped child, psutil on your own child, and the keyword-only call site make such an exception practically unreachable. Any leak is bounded by `sleep(30)`, and the test fails loudly anyway. Guarding it would be over-armoring ([WT-4.4]). | **Reject; no change.** If the owner disagrees, the smallest fix is to append `process` to a list first and `kill()`/`wait()` it in `finally` when `poll() is None`. |

## Your three questions

1. **Correctness: yes.**
   - The test observes the real acquisition handoff and passes the call through unchanged. It still fires against the actual defect: in your missing-rollback control, both cases failed exactly `assert not child.is_running()`. That is supplied evidence, consistent with my reading.
   - The negative assertion has positive support: the child is live at handoff, and `sleep(30)` outlasts the 10 s guard, so without rollback the child must still be alive when asserted. The only way it passes vacuously is if the interpreter crashes on its own.
   - Your marker-fault controls show the dependency that masked the failure is gone, not hidden. They don't diagnose the Windows cause, and you correctly don't claim they do.
2. **Expert elegance: yes.** Watching the existing Host-to-supervisor seam is the direct move. It removes a file handshake whose readiness was never part of the contract, adds no helper or production seam, and makes the test shorter.
3. **Underlying primitives and data structures: yes.** The test uses the `Popen` handle for liveness and PID stability while the process is unreaped, then psutil `(pid, create_time)` for death checks after reaping. That pairing matches Golden Rule 14's scoped-identity rule. No file, pipe, socket or event handshake remains.

**Does dropping the handshake weaken the bug detector?** Not for the [RM-5.1] obligation, which covers the acquired root process (see O1).

## Observations (not actionable in this pass)

- **O1: Windows venvs.** On Windows, `sys.executable` in a venv may be a redirector that starts the real interpreter as a child. This is inference; I haven't checked the uv venv used in CI. If so, the old marker pinned that descendant, while the new test pins the `Popen` root.
  - [RM-5.1] makes the root the obligation and explicitly doesn't widen tree enforcement, so the new pin is the exact contract.
  - Production rollback still kills the whole tree (`terminate_process_tree`), but this test no longer asserts that descendants die. Optionally say so in the T6 completion note.
- **O2: redundant `create_time()`.** The explicit `child.create_time()` at `:156` duplicates what psutil 7 already does at construction. It was in the baseline and this change doesn't make it worse.
- **O3: plan status.** Plan line 2272 still calls T6 "proposed". Reconcile it when Windows CI results come in. The plan is outside this unit.
- **O4: completion gate.** You haven't claimed a Windows pass, and the plan (`:591`) makes one a T6 acceptance condition. The completion gate stays open until it passes.

No standing constraint moves: [RM-5.1], [TS-0], WT-2/WT-4, custody contracts and [ALT-WT-017] (no reduced workers) are all unchanged.

</details>

Final refinement source SHA-256: `85ede730a503fbc48f0faec64c2cb36e06f992e53bf8ca2854a6e77c06d12bcf`. The affected file passed all 10 cases in 6.60 seconds; the canonical PostgreSQL KEEP/CLEAR pair passed in 1.93 seconds. Ruff, format and scoped mypy passed. Normal 12-worker settings were retained; all-extras was restored and the owned PostgreSQL container removed.

Author fresh-eyes pass: ownership is recorded before observation, the original supervisor and monitor validation remain real, and cached returncode cannot reap the process being checked. The explicit poll-only alternative falsely passed both kill-without-reap controls; the final oracle failed those and missing rollback for both policies. Normal and marker-fault controls passed. Independent cleanup reaped all ten control Popen objects without rescue. Controls differ from final only in the assertion diagnostic text. Evidence and exact commands: `/tmp/weft-implementation-20261027/t6-popen-review-evidence.md`. This is stronger root ownership evidence with fewer primitives, not descendant coverage. Final focused cross-family review and corrected-head Windows CI remain open.


### T6 final focused cross-family review (2026-10-28)

Claude completed in 174.31 seconds with required context read, no findings in scope and no blocker. Correctness, elegance and use of underlying primitives all pass. Author accepts the explicit root/owned-handle proof boundary. O1 is a required diagnostic constraint for Windows: investigate any bounded rollback expiry; do not relax the oracle. O2 records ownership semantics. O3/O4 are pre-existing cleanup-path observations, not expanded in this narrow change; O5 records descendant coverage limits. O6 remains open pending current-head CI and plan closure.

<details><summary>Verbatim final T6 refinement review</summary>

# T6 Popen refinement review: `test_monitor_validation_failure_finalizes_consumer_and_reaps_command`

**Verdict: no blocker.** I found no findings inside the unit. There is no `RECONSIDERATION REQUESTED`. Windows CI is still required before T6 is complete.

## Context completion

I read the full required context, in this order:
1. `docs/program-theory.md`
2. `docs/agent-context/README.md`, `decision-hierarchy.md`, `principles.md`, `engineering-principles.md`
3. Runbooks: `testing-patterns.md` and `review-loops-and-agent-bootstrap.md`
4. `docs/agent-context/lessons.md`
5. `docs/lessons.md`. The watermark in `docs/coalescing.md:91` is "(none)", so I read the Golden Rules and every dated section, through the working-tree 2026-10-28 entry ("ownership starts at acquisition", "cleanup must remain effective when the method being tested is broken").

I also read the governing contracts:
- `06-Resource_Management.md` [RM-5.1], lines 123–175, especially 168–175 ("stops and reaps the acquired root process … does not broaden the … process-tree enforcement boundary").
- `08-Testing_Strategy.md` [TS-0].
- Plan T6 at `2026-10-27-runtime-and-test-repair-plan.md:576-595` and the refinement note at `:2266-2295`.

There is no module theory for runners or Consumer. The only theory files are the program theory and two plans, so this is recorded as absent, not skipped. I read no other runbooks because they don't apply to a test-only delta.

I ran nothing and computed no hashes. Anything I attribute to "supplied evidence" comes from your package. Everything else comes from reading the repo, and I label stdlib-behaviour points as inference.

## Verification of the requested points (my reading)

**Host call site and module-local binding**
- `host.py:46-49` imports `run_monitored_subprocess` into Host's own namespace.
- Command dispatch goes `host.py:487-488` → `_run_command_with_hooks`. That function creates the real `Popen` at `:936` (text pipes, so `Popen[str]` matches the annotation), checks `_host_handle` at `:967-969`, then makes one bare-name, keyword-only call at `:977-994`.
- Patching `host_module.run_monitored_subprocess` therefore intercepts exactly the handoff, with the process already acquired.
- Global `subprocess.Popen`, the validator and the supervisor body are untouched. The `**kwargs` wrapper would fail loudly if the call ever went positional.
- `psutil` and `contextlib` are still used later in the file (`:214-328`), so keeping those imports is correct.

**Supervisor ordering**
- Inside `subprocess_runner.py:107`, the order is: callbacks (`:108-117`), then `load_resource_monitor` (`:119-124`), then `TypeError` at `resource_monitor.py:273-276`.
- The `except BaseException` at `:425-431` calls `_cleanup_after_execution_failure` (`:447-471`), suppresses errors from it, and re-raises the original.
- The cleanup sequence:
  1. Stop: `terminate_process_tree`, then `process.wait(0.2)` (`:474-484`).
  2. On timeout, the kill path: kill tree, `wait`, `kill`, `wait` (`:487-497`).
  3. Then `poll()`; if the process is still alive, `kill` and `wait` (`:462-471`).
- `finally` closes pipes and stops the monitor (`:432-444`). The wrapper only adds steps before all of this.

**Cached returncode oracle**
- `Popen.returncode` is set only by the handle's own `poll`, `wait` or `communicate`.
- Every production rollback path ends in `Popen.wait` or `Popen.poll` on the owned handle.
- On POSIX, `terminate_process_tree`'s `psutil.wait_procs` may `waitpid` the child first. A later `Popen.wait`/`poll` then gets `ECHILD`, and CPython sets `returncode = 0` (inference from CPython's `_try_wait`/`_internal_poll`). So the oracle can't give a false red against today's code.
- The only non-product `poll()` before the assertion is the wrapper's. If it ever reaps, it does so only on a path where its own `assert … is None` already fails the test.
- So "the owner handle recorded exit" can only be satisfied by product rollback. Your kill-without-reap control (supplied evidence) shows the poll-oracle false green is real and that this oracle closes it.

**Consumer lifecycle and error propagation**
- The runner executes on the in-process worker lane, so the patch applies there.
- `consumer.py:376-386` captures the exception. `:528-534` → `_finalize_work_exception` (`:271-295`) marks the task failed, sets `state.error` and emits one `work_failed` through the terminal finalizer.
- `cleanup_on_exit=False` leaves runner rollback as the only thing that kills the child.
- If the wrapper's assertion ever fires, its message lands in `state.error`, and the first assertion (`:173`) shows it.

**Kept assertions**
- Lines 173–183 still check: the real `BaseResourceMonitor subclass` text, exactly one `work_failed`, `CLOSED`, KEEP/CLEAR reserved custody, a recorded child, and the reap.
- The 10 s `drive_until` guard and the worker count are unchanged. No wait was added.

## Does removing the handshake weaken the bug detector?

**No. It strengthens it.**
- The [RM-5.1] obligation is the acquired root, and `poll() is None` at handoff proves the root is live at the point the contract cares about.
- The prior review's only vacuous-pass route was "the child exits on its own before the check". That route is now closed: with rollback missing, nothing on the owner handle sets `returncode`, so the assertion fails even if the child died independently.
- Supplied evidence matches this reading: missing rollback and kill-without-reap fail exactly `assert child.returncode is not None` under both policies.

## Do the controls exercise production and preserve cleanup?

**Yes (my reading of the supplied plugin).**
- Each mutation replaces a module-global name that production looks up at call time, so the mutations take effect: `subprocess_runner.load_resource_monitor` (called at `:120`) and `subprocess_runner._cleanup_after_execution_failure` (called at `:428`).
- The candidate source really ran on both xdist workers: the tracebacks point at `/tmp/.../t6-popen-returncode-candidate.py:181` on gw0 and gw1.
- Kill plus stdout EOF with no wait is a sound "exited but unreaped" construction for a single-process root on macOS.
- The test's `finally` uses the retained `Popen` (`poll`/`kill`/`wait`), which works even when rollback is broken. Supplied evidence shows `needed_rescue` and `alive_after_cleanup` empty for all 10 recorded processes.

## Findings

None within the unit.

## The three answers

1. **Correctness: yes.** The test watches the real ownership boundary, records the owned handle before anything that can raise (this resolves the prior F1), and forwards the call unchanged. The real validator still fires. The completion oracle can only be satisfied by the product's own wait or poll. It also no longer depends on psutil re-reading the process's identity.
2. **Expert elegance: yes.** It is a four-line pass-through at the existing seam: no file, pipe, socket or event handshake, no helper, no production seam, and a shorter test. The comment at `:180` states the one non-obvious point: don't let the oracle do the reap it is checking for.
3. **Underlying primitives and data structures: yes.** `Popen` is the handle the product owns, and `returncode` is its own record of exit. Using that, rather than OS probes or a PID the child publishes itself, follows [WT-4.2] "one owner for each resource" and "keep an identity you already have".

## Observations (not actionable in this pass)

- **O1: interpreting a Windows failure (affects this delta).** On Windows, the old psutil `is_running()` oracle would pass if the process died after production's bounded waits expired. The new oracle requires the product to have observed the exit within those waits: about 0.2 s per step, roughly 1 s or more of total grace after the kill (`_constants.py:1911`). That stricter requirement matches [RM-5.1]'s "stops and reaps". If Windows CI fails on `:181`, treat it under [TS-0] as a possible product rollback-budget expiry and investigate it. Don't relax the oracle.
- **O2: oracle scope.** The oracle means "the owned `Popen` recorded exit", which is stricter than "the OS reaped the PID". A future rollback that reaps only through psutil would fail this test. I consider that correct, because the handle is the owner, but it's worth knowing.
- **O3: cleanup order (pre-existing, unchanged).** In `finally`, `task.cleanup()` runs before the child reap loop (`:185-189`). If `task.cleanup()` ever raised, the 30 s child would outlive the test. This ordering is the same as at baseline `40f094c3`, so the delta doesn't make it worse. Under the 2026-10-28 lesson, the smallest fix would be to reap the children first, or nest a `try/finally`. That's for the owner to decide.
- **O4: unclosed pipes.** If the wrapper's own assertion fails, the product never runs, so the pipes are left to garbage collection. No `ResourceWarning` filter is configured in `pyproject.toml`, and that path already fails the test. Negligible.
- **O5: descendant coverage.** Coverage is root-only, as you correctly say. A Windows venv redirector descendant isn't covered by this test; production still kills the whole tree (`helpers/__init__.py:489`, `:572`).
- **O6: completion gate.** Windows CI and the plan-status reconciliation (`:2266-2295`) are still open.

No standing constraint moves: [RM-5.1], [TS-0], WT-2/WT-4, durable queue/state/custody contracts and [ALT-WT-017] are all unchanged.

**Verdict line: no blocker.**

</details>

Final local integration checks: mypy passed 478 files; Ruff check, formatting (631 files), suppression registry and whitespace checks passed. Six plan/spec hygiene cases passed in 0.94 seconds and documentation paths passed. The focused SQLite/PostgreSQL evidence above is on the final test. No runtime behavior changed in T5/T6, so the existing full runtime suites remain applicable; new-head complete CI remains required.


### Windows T6 CI confirmation (2026-10-28)

At pushed head `8261a4b09f3dd2dcb92af02d3f5b926ab9efe10d`, Actions run `40730441608`, Windows3.12 remaining job `122987338501` passed, including the repaired process-ownership regression. This supplies actual Windows validation for T6. It does not establish the original marker-access failure's OS cause, and does not certify later admission commits. PostgreSQL remaining job `122987337842` started; other jobs and dependent extension checks remain pending. No merge or plan closure.


### T7: quiet-output CI diagnosis (2026-10-28, in progress)

Current repair head `63dd4752fdff1e3a1da42187d881d0f1942565ce` failed
Actions run `40743696948`, PostgreSQL remaining job `123012269327`, at
`tests/tasks/test_task_interactive.py::test_production_driver_delivers_quiet_output_only_through_notify[muted]`:
`assert delivered() is notify` observed True while notify was False. All 27
OS/Python test matrix jobs, the other two PostgreSQL jobs, lint and coverage
passed; the four dependent extension/integration jobs were skipped, not passed.
The original failed log is retained as
`/tmp/weft-implementation-20261027/ci-40743696948-failed.log`.

This remains part of the existing class-5 repair unit, with no proposed product
contract change. Governing behavior: `01-Core_Components.md` [CC-2.2.1],
`07-System_Invariants.md` [IMPL.8]–[IMPL.11], and `08-Testing_Strategy.md` [TS-0].
The reactor legitimately handles broker, local and timer inputs. Muting one
source does not forbid turns caused by another source.

Read-only diagnosis identified an unproved test premise: its barrier announces
quiet waiting at entry to `_wait_for_activity_body`, before the retained
strategy consumes the local wake armed by initial stdin delivery. The real
`MultiQueueWatcher._drain_queue` arms that wake; SimpleBroker
`PollingStrategy.wait_for_activity` consumes it immediately; Weft
`_activity_hint_action` returns to the reactor on a local hint. A captured output
frame can therefore be delivered on that legitimate follow-up turn with its
own notification muted. The CI log does not identify its exact wake cause.

Before correction, force this ordering using the real initial inbox wake:
assert it is pending, release the child and wait for its actual capture callback,
then forward the original wait. Require the original muted assertion to fail,
not a setup or timeout failure. No additional wake, state clearing or substitute
reactor is permitted. If confirmed, move test release to the actual strategy
wait boundary after startup's local hint and follow-up turn; preserve the real
wait/callback path. Prove capture before the muted absence assertion. Do not
reduce workers, relax timing, change runtime, or classify a passing rerun as a
cause. Validate both parameters and the owner/sibling suite on SQLite and
PostgreSQL with the standard 12 workers, apply a notification-removal sensitivity
control, obtain fresh-eyes and cross-family review, then rerun original CI load.
Admission measurements and local runtime probes run sequentially.

T7 diagnostic confirmed the exact bad oracle on both backends: the controlled
original-order probe failed `assert delivered() is notify` with True versus
False (SQLite 1.42 s, PostgreSQL 2.10 s, normal 12 workers). The prerequisite
assertions proved the real startup local hint and actual capture, with no
injected wake or hint clearing. This confirms a source-permitted causal path;
the original CI log still does not identify that run's actual wake.

The correction observes the retained strategy's hint-free wait after session
readiness, then releases the child. Both cases observe the real capture callback;
muted absence starts only after capture. The positive delivery budget stays
five seconds from release; the muted interval stays 0.5 seconds. A sampling
deadline crossing fails rather than passing via an unrelated timer. Full
interactive module GREEN: SQLite 30 passed in 3.34 s; PostgreSQL 30 passed in
5.52 s. The isolated Consumer callback-disconnection mutant failed the intended
`assert delivered() is notify` (False versus True) in 6.52 s after capture;
production was never mutated in the main checkout. Scratch source and log:
`notify-no-consumer-wake/`, `notify-no-consumer-wake.log` in the evidence directory.
Focused Ruff and mypy passed for both touched test modules.

Author fresh-eyes: the original barrier confused entry with blocking. Retaining
startup's actual hint consumption and follow-up turn is the smallest correction;
clearing a hint or padding an absence window would alter the event model. The
callback is shared with EOF/exit, but this fixed child emits only the selected
stdout line and remains alive; no extra stream abstraction is justified.

Tier-1 contained Claude implementation review completed in 214.78 s: no blocker,
correct/elegant/appropriate primitive reuse. F1 accept: explain the hint-free
wait's causal ordering at the edit point (comment updated). F2 reject expansion:
this fixed producer never writes stderr and remains alive, so a stream-specific
observer would add machinery without protecting a distinct exercised contract.
The reviewer read the actual installed SimpleBroker wheel, not the neighboring
source it could not access. Supplied execution evidence is not reviewer execution.
Original-load full suites and latest-head CI remain open.

<details><summary>T7 independent review verbatim</summary>

**Verdict: no blockers. No blocker Fids.**

I read the current diff, the Consumer, BaseTask and MultiQueueWatcher wait paths, the host runner and the governing spec text. My file tools are confined to this repository, so I could not open `/Users/van/Developer/simplebroker/simplebroker/watcher.py`. I read the copy the tests actually import instead: `.venv/.../simplebroker/watcher.py`, installed from a wheel as simplebroker 8.7.2, not an editable link to that path. I did not run anything.

## Source and premise checks

Each claim in the plan matches the source:

- **Startup wake is armed.** `multiqueue_watcher.py:1587-1588` arms a local wake (`notify_activity()`) whenever a drain pass processes at least one message, so initial stdin delivery arms it.
- **The strategy consumes it at once.** `PollingStrategy.wait_for_activity` consumes a pending local wake at `watcher.py:1411-1413` before any waiting. `start()` (`1576-1593`) does not clear `_local_activity_pending`.
- **A local hint always returns to the reactor.** `_activity_hint_action` returns on a local hint (`multiqueue_watcher.py:1204-1207`). Weft never calls `mark_local_activity_as_empty_check`, so that path cannot downgrade the hint.
- **Output is queued before the callback.** The host reader calls `target_queue.put(chunk)` and then `on_activity()` (`host.py:1102-1103`), matching CC-2.2.1 (`01-Core_Components.md:339-343`). The comment at test line 471 is accurate.
- **Muted case has no hidden wake.** Consumer does not override `_has_pending_observation`. Backend and data-version hints, and native idle polls, only hand control back to the reactor when input messages are pending, and the outbox is not an input. With the process title disabled, the only remaining deadline is the sampling timer, which `Consumer.next_wait_timeout` exposes.
- **The patch hits the real object.** `_strategy` is assigned once (`watcher.py:425`), so patching that instance covers every wait. The body passes `timeout=` as a keyword, which the wrapper's signature accepts.

## Correctness of the new boundary

- **The gate proves the follow-up turn finished.** The session, and therefore `child_ready`, can only exist after the dispatch turn. That turn armed a hint, and consuming a hint always gives the reactor another turn. So if `child_ready` exists at a strategy wait with no pending hint, both the dispatch turn and its follow-up turn are done. If the gate fires too early, the `child_ready` poll asserts on the reactor thread and the test fails loudly. It cannot pass silently.
- **The sampling-deadline check errs on the strict side.** The test's `sampling_deadline` is computed later than the reactor's own deadline, so it is never later than the real wake. If `now < sampling_deadline`, no timer wake can have happened. Any error produces a false failure, never a false pass.
- **The test is falsifiable both ways.** In the muted case, `captured` proves the chunk was queued before the 0.5 s absence window starts. In the notify case, delivery can now only come from the notification, so the planned notification-removal control will discriminate.
- **Unexpected states fail rather than hang.** If `_local_activity_pending` were renamed, the reactor raises `AttributeError`. If the gate never fires, `reactor_waiting.wait(10.0)` fails and the observer's `finally: task.stop()` stops the reactor.

## Findings

**F1 (non-blocker, low). Suggested: modify.** The comment at lines 456-457 states the conclusion but not why it holds. The `child_ready` poll inside the reactor-thread hook is what proves the ordering. Moving it to the observer thread looks like an obvious tidy-up, but it would bring back the original race. Suggested wording:

> "A hint-free strategy wait after the session exists follows the dispatch turn, whose drain armed a local hint that always returns for a follow-up turn."

This is a comment-only change.

**F2 (owner decision; expansion, not a blocker). Suggested: reject unless cheap.** `captured` fires on any `on_activity` call, including stderr chunks, end-of-stream and process exit. `is_alive()` rules out the exit path. A stderr chunk before release is the only theoretical false capture, and this child never writes to stderr. Narrowing it would need a stream-specific seam, which isn't worth the added complexity.

## Answers to your questions

1. **Correct?** Yes. The causal premise holds in source, and the new boundary sits exactly after the startup hint and its follow-up turn. It keeps the real wait, the real callback, the real child and the real timer composition, and it adds no wake, clears no state and substitutes nothing.
2. **Elegant, expert systems programming?** Yes. One gate at the true strategy boundary, one capture event, and one check for the sampling deadline. The `release_at` versus capture-time asymmetry is deliberate: the notify case measures latency from release, the muted case measures absence after capture.
3. **Proper reuse without overcomplication?** Yes. It reuses the existing `TaskRunner.start_session` hook point (`start_observed` wraps it for both cases), `next_wait_timeout()`, and the strategy's own pending flag. No public read-only accessor exists for that flag. Reading it is the accepted test seam, not a production change. There is nothing to remove: the `_interactive_session is not None` assert is needed so mypy accepts the `is_alive()` call.

## Non-actionable observations

- The test reads `_interactive_session` and calls `stop` from the observer thread. That predates this change and is not made worse.
- The planned validation and CI rerun are still pending. I did not check the diff against ruff/mypy. Its longest lines are 87 characters.

</details>

### T7 original-load verification update, 2026-10-28

Full SQLite passed5982tests with38skips in232.37s. Full PostgreSQL stopped after4751passes29skips on the separate admission startup-pressure setup, documented in the admission plan. The T7 notification regression did not fail in that run; the incomplete run is not a full PostgreSQL pass. Corrective-head CI and extension jobs remain required.

Original-load PostgreSQL rerun after the startup-pressure setup correction:5934passed29skipped in338.95s (`c1-full-postgres-pressure-fixed.log`). No worker count, deadline, pressure or runtime behavior was relaxed. This closes the local full-PG gate for the current combined working tree; corrected-head CI remains separate. Full SQLite was5982passed38skipped232.37s before the PG-only setup correction.

### Corrective-head CI result and Windows diagnostic boundary, 2026-10-28

Pushed a385f3e86d620f3dcf783e535a22c0f221394382, run40769090681: all three PostgreSQL jobs, 26 of27 OS/Python jobs, lint and coverage passed. The original notification test passed in PostgreSQL remaining. Windows3.14 CLI job123101422111 failed because worker gw2 exited during `test_installed_console_function_handoffs_from_fresh_project`:292passed10skipped, one worker-loss failure. Django, Docker, macOS-sandbox and microsandbox dependent jobs were skipped, not passed.

The installed-console test has a90-second module marker, overriding the workflow900-second default. Worker loss occurred approximately90.19seconds after controller logstart, strongly consistent with timeout but not a captured timer-fired event. Source tracing shows pytest-timeout thread expiry prints through the worker terminal writer and then calls os._exit(1); execnet worker stdout goes to the null device, so stacks may disappear. No stack or last test phase was retained. The cause could be a slow command, teardown, cumulative healthy work or another termination mechanism. Do not label it an interpreter crash, connect it to POSIX reap ownership, or relax timeout/workers based on this evidence. Diagnostic proposal and source trace are in `windows314-installed-console-ci-diagnosis.md`; a reviewed diagnostic-only change is required before diagnosing the blocked owner from a new full-load Windows run. No merge or final acceptance.

### CI-D1: retain Windows worker-timeout evidence (proposed)

# Retain Windows xdist timeout evidence (proposed slice, 2026-10-28)

Status: cross-family plan review PASS; F1–F6 accepted below before implementation. RED-only acceptance is the next gate; the diagnostic plugin remains unchanged until the root verifies the intended RED assertion.
Class: 4 under [DOM-15]: narrow multi-surface test/CI observability work touching pytest worker teardown. Source spec: `docs/specifications/08-Testing_Strategy.md` [TS-0]. Existing owning plan: `docs/plans/2026-10-27-runtime-and-test-repair-plan.md`; append this slice and its review/evidence there, not a new product runtime plan. No normative behavior change is intended; if a review finds one, reclassify before editing.

## Outcome and evidence boundary

Retain the existing pytest-timeout worker output in a persistent artifact so the next original-load Windows 3.14 CLI failure identifies the actual blocked stack/phase. Preserve test outcome, 90s item timeout, workflow 900s default, full CLI selection, normal worker-count computation (the failed runner selected five), xdist scheduling, command deadlines and cleanup behavior. No watchdog, new failure timer, retry, reduced-load acceptance, product runtime edits, or concurrency work.

Known: the failing module's `timeout(90)` overrides CLI900, and thread timeout covers the full test protocol. The controller's heartbeat and worker-down timestamps suggest ~90.19s. Not yet known: whether pytest-timeout fired, which operation consumed time, whether the root cause is product or test, or whether a healthy aggregate of nine subprocesses plus cleanup exceeds90. A passing diagnostic rerun establishes intermittence only.

## Verified source seams and lifetime

Failure environment: pytest9.2.0, pytest-timeout2.4.0, pytest-xdist3.8.0, execnet2.1.2, Python3.14.9. Local installed pytest is9.1.1. The root has now inspected the exact pytest9.2.0 wheel and verified the same `Config.get_terminal_writer` → reporter `_tw` → writer `_file` path; the source-version prerequisite is satisfied, with provenance below. This source check does not establish Windows runtime behavior. Verify versions again from new CI installation logs; do not assume stability.

- `pytest_timeout._get_item_settings`: closest marker first, fallbackCLI only for absent values. `pytest_runtest_protocol` starts the existing thread timer before setup when func_only=False. `timeout_timer` writes captured streams and `dump_stacks` using `item.config.get_terminal_writer()`, flushes, then calls `os._exit(1)`.
- Verified in local pytest9.1.1 and extracted pytest9.2.0, `Config.get_terminal_writer()` returns `TerminalReporter._tw`; its TerminalWriter stores the stream in `_file`, uses `_file.write` and `_file.flush`, and has no public stream replacement API. `TerminalReporter` was initialized against worker sys.stdout.
- Execnet `init_popen_io` redirects worker fd1 toNUL and replaces sys.stdout with that stream. The existing timeout writer therefore has a sink that cannot reach the controller's reports.
- Existing `tests/helpers/run_diagnostics.py::_RunDiagnostics` owns per-process files and closes idempotently in sessionfinish/unconfigure. Keep ownership there. The actual extension point will be the one existing writer's `_file`: save it, assign a fail-open retained stream only for an xdist worker, restore it before closing any file. Do not replace Config.get_terminal_writer, pytest-timeout's timer hook, terminalreporter, or controller output. Isolate the private seam at one installation/restoration site with a comment naming the verified pytest version relationship and a real plugin integration test.

## Minimal implementation

1. Extend `_RunDiagnostics` with its `config`, an optional worker terminal-log file, original writer/stream references, and installed-stream reference. Open `<worker>-<pid>.terminal.log` only when `workerinput` is present, in the existing `pytest_configure` construction/registration ownership scope beside `.stacks.log`, with `encoding="utf-8", errors="backslashreplace"` and line buffering. On a later open or registration failure, close every already-acquired diagnostic file before disabling the plugin; ownership begins at each successful acquisition, not at completed construction. A controller keeps today's terminal writer. In the existing `pytest_sessionstart(self)`, find the reporter with `config.pluginmanager.get_plugin("terminalreporter")` and its writer with `getattr(reporter, "_tw", None)`, rather than the asserting `get_terminal_writer()`. Install only a compatible existing stream. Keep `.stacks.log` for fatal faulthandler evidence, and record sink installation/unavailability in the existing JSONL owner.

   The retained stream needs a small fail-open write/flush adapter because a raw file flush failure inside pytest-timeout's `finally` would prevent its subsequent `os._exit(1)`, changing failure semantics. On OSError/ValueError from diagnostic write/flush, stop using the failed diagnostic sink and forward to the original stream. Do not swallow arbitrary programming exceptions. The original stream remains open and is never closed by this plugin. Keep the adapter local to run_diagnostics with only write and flush: writer formatting/isatty settings are already cached. UTF-8 backslash replacement prevents a lone surrogate from raising UnicodeEncodeError (a ValueError subclass) and incorrectly disabling a healthy sink. Preserve the existing writer object and formatting settings by swapping its stream, rather than constructing a replacement reporter or copying its settings.

   Install only after all resources/references are ready. Missing/incompatible terminal writer/private stream means record diagnostic unavailability and leave the original path untouched; never fail the test. On `_close`, if the writer still owns this adapter, restore its original stream first, then close the retained file. Do not overwrite a later plugin's stream change. Close remains idempotent. A hard worker exit cannot run restoration, but the timeout callback flushes the retained file before its original os._exit. Fatal faulthandler stays independent.

2. Add opt-in phase records to only the installed-console acceptance test, through the existing `_RunDiagnostics` record owner. Do not create another JSONL writer. Use a small public recording method on that plugin or a narrow module function that retrieves `weft-run-diagnostics` from the test's request.config and calls its existing recorder. No-op when absent. A public helper is warranted only as the real consumer seam, not a test-only export.

   Record fresh initialization, each console command, harness-enter and harness-exit begin/return or raise, with nodeid/phase, monotonic elapsed and command argv/returncode. Never log environment dictionaries or credentials. Log subprocess timeout/exception type before rethrowing unchanged. Keep actual subprocess.run invocation, its captures, cwd, env and deadline unchanged. The eight existing `_run_console` calls share the same forwarding record wrapper. The duplicate initialization subprocess can use `_run_console(console,tmp_path,'init',str(root),env=env)` with the exact old30s timeout/cwd/argv. Do not rewrite harness lifecycle: explicit phase points surround the existing with harness boundary, and `finally` after it records completion/failure without suppressing exceptions. Remember the existing init-failure test directly calls this test function; if request becomes required, forward its existing request explicitly so that coverage remains real.

   Enabling this directory enables the entire existing diagnostic plugin: fatal faulthandler stacks move into `.stacks.log`, harness exception diagnostics use this retained directory instead of disposable temporary paths, and pytest controller/workers write JSONL events. The upload must retain all of these files; inline fatal stacks may no longer appear in the job log. Product commands do not load this pytest plugin.

   Phase instrumentation is diagnostic only. It should not read broker state, poll, spawn or add waits. It can identify the last entered owner even if os._exit bypasses finally; actual blocked stack comes from the retained timer dump.

3. In `.github/workflows/test.yml`, enable `WEFT_TEST_DIAGNOSTICS_DIR` only for Windows CLI matrix jobs, rooted under runner.temp with a job-specific subdirectory. Add an `if: always() && matrix.os == 'windows-latest' && matrix.test_slice == 'cli'` artifact-upload step, using the existing pinned `actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9`. Unique artifact name includes Windows, Pythonversion andCLI. Upload that same directory; missing files warn, never convert a failed test to success. Do not change pytest command, markers, worker-count step, fail-fast setting, capture mode or timeout args. Keep artifacts outside harness roots so cleanup cannot remove them.

## Falsifiable verification and review gates

Before edits, root applies this reviewed slice to the runtime repair plan and records its fixed source baseline. Cross-family plan review verifies the stream lifetime/failure fallback, no timer-policy change, phase ownership, and correct upload path/condition. Fresh-eyes review is separate. Do not run tests alongside exclusive admission measurements.

Use existing `tests/system/test_run_diagnostics.py` real subprocess acceptance. Extend its `_run_test` command construction with an explicit opt-in pytest-timeout plugin argument for the new probe (autoload remains disabled), retaining xdist.plugin and `-n1 --max-worker-restart=0` for that subprocess. One worker is the existing diagnostic-fixture boundary, not a reduction of the actual Windows CLI acceptance load. Run the outer repository test with normal configured workers.

New timeout probe: a temporary test with `@pytest.mark.timeout(1,method='thread')` blocks inside a named function (`test_timeout_probe`, e.g. time.sleep60) and prints a unique captured-output token. The outer subprocess limit remains30s, independently bounding a broken diagnostic adapter. Require result.returncode1 and the same controller worker-crash report. With diagnostics enabled require exactly the crashing worker's terminal artifact, containing a Timeout banner, the unique captured token, and the named test in a thread stack. Require the existing JSONL test_start and controller worker_down. These are actual timer/xdist/TerminalWriter effects, not mock callback output.

- **RED:** run that acceptance case against the unchanged diagnostic plugin. Before the intended failure, assert child exit1, the exact crashed item, and that neither a Timeout banner nor the unique captured token appears in controller stdout/stderr. The intended RED is the terminal-artifact count being zero rather than one, proving absent retained content. A startup/import/plugin/outer30s failure does not qualify.
- **GREEN:** corrected plugin retains banner/token/stack, child exits1 as before, controller reports the same crashed item. No fresh timeout machinery is installed by our code.
- **Sensitivity:** restoring only the original writer stream must make artifact-content assertion fail; restoration of candidate returns GREEN. Do not accept merely nonzero subprocess exit as evidence.
- **Normal close:** extend existing passing-session test with distributed=True (preserve standalone arm) and require normal result0/sessionfinish/no INTERNALERROR. This verifies the normal outcome and worker terminal_sink_installed event. Restoration before close and idempotent unconfigure are verified by source inspection; fail-open fallback means a successful run does not prove restoration. No private-field equality-only test is added.
- **Disabled/unwritable:** reuse `test_run_diagnostics_never_replaces_test_failure` unchanged for absent env and path occupied by a file; original assertion text and exit1 stay intact, no INTERNALERROR. A distributed duplicate does not exercise an additional boundary because the invalid directory fails before construction. Partial-open failure needs a focused ownership control proving already-open files close, not another invalid-directory case.
- **Post-open sink failure:** from inside the child test, use `request.config.pluginmanager.get_plugin("weft-run-diagnostics")` to close the retained terminal file after installation, then block in the same real thread-timeout probe. The first write reaches the closed sink and switches later writes/flush to the original stream. This does not separately test the defensive flush exception guard. Preserve the 60s child block and 30s outer limit: an escaping flush would skip os._exit and hit the outer limit. Require exit1, the crashed-item report, and no ValueError or Traceback text in stderr. This is narrow dependency fault injection, not replacement of the timeout path; a mock write-call count is insufficient.
- **Phase forwarding:** existing installed-init-failure test must still preserve original failure and avoid materializing harness context. A small existing diagnostics subprocess fixture can call the public phase recording seam then fail, asserting recorded begin/exception and unchanged outcome; do not duplicate full installed-console work merely to prove JSONL formatting. Actual next Windows run proves the real invocation records.

Then Ruff/format/mypy/doc-policy checks appropriate to changed files, fresh-eyes and cross-family implementation review, and the original full Windows3.14 CLI job (normal computed five workers on the observed runner). AllWindowsCLIversions can receive same diagnostics without changing runtime acceptance. Do not claim success from a focused lower-load standalone console test.

## Interpret the next evidence

Captured Timeout banner at90 confirms the termination mechanism. The stack plus unmatched phase then decides the next repair:

- healthy command completions followed by aggregate90 expiration: inspect whether independent contracts should be split while retaining the fresh-project and same-manager reuse witnesses; don't infer this merely from nine calls;
- particular CLI stuck or30s TimeoutExpired: inspect that product/bootstrap/wait owner, preserve its output and cleanup evidence;
- cleanup phase stack: investigate that cleanup owner and deadline accounting;
- no timer banner but fatal stack or different exit: follow that evidence rather than the90s coincidence;
- passing rerun: record intermittence, not a cause or fixed status.

Risks accepted for this diagnostic slice: a private pytest writer seam isolated behind opt-in fail-open handling; extra small file writes can perturb timings, so diagnostic timing is not admission performance evidence; hard OS termination can prevent even a flush, so absent artifacts remain inconclusive. No unrelated diagnostics framework, timer ratchet or product behavior change is included.


Root source-version verification: downloaded the exact PyPI pytest9.2.0 wheel, verified SHA256 `07acdcf88ec174f907dc01af70bf52d49b014bbab03faeeade31c96b18c2e588`, and read `_pytest/config/__init__.py::Config.get_terminal_writer`, `terminal.py::TerminalReporter.__init__`, and `_io/terminalwriter.py::{write_raw,flush}`. The same reporter `_tw` / writer `_file` seam is present; write_raw calls only stream.write and flush calls stream.flush. Replacing the stream after construction preserves cached formatting. The preceding source-version prerequisite is now satisfied; this does not establish Windows runtime behavior. Provenance and extracted files are retained outside the repository at `/tmp/weft-implementation-20261027/pytest-9.2.0-source/` and `/tmp/weft-implementation-20261027/pytest-9.2.0-provenance.json`.

Author fresh-eyes: persisting the existing timeout output is narrower than adding another watchdog. Opt-in ownership and restoration before close are essential, because a new stream failure must not interrupt timeout finalization. The real subprocess probe must fail on missing retained contents rather than mere child failure. Phase records reuse the existing recorder and must never read the broker or alter command/harness cleanup. The private seam and small diagnostic timing perturbation are explicit residual risks. Cross-family plan review returned PASS, with the accepted robustness and precision changes below; implementation review remains required.


#### CI-D1 cross-family plan review dispositions, 2026-10-28

| Finding | Disposition and operative correction |
|---|---|
| F1 | Accept. Open the UTF-8 terminal log with `errors="backslashreplace"`; an encoding error must not disable the sink. |
| F2 | Accept, with explicit acquisition ownership. Open the worker file in the existing configure/construction scope, pass config into the plugin, inspect the optional reporter without assertions, and close already-open files if later construction or registration fails. Retain the existing disabled/unwritable tests rather than a redundant distributed variant. |
| F3 | Accept. RED first proves the child exits1 and the controller lacks the banner/token, then fails specifically on zero retained terminal artifacts. |
| F4 | Accept. The real child test closes the installed terminal file and reaches the existing timeout. Require unchanged exit/crash behavior and no diagnostic exception; the60s block versus30s outer limit detects a flush that prevents exit. |
| F5 | Accept. The operative plan names all opt-in effects: fatal stacks, harness failure snapshots, and pytest event JSONL move into the retained/uploaded directory. |
| F6 | Accept. Exact pytest9.2.0 source verification is marked satisfied; scratch provenance paths are absolute and explicitly outside the repository. |

Fresh-eyes disposition: accept the independently identified partial-acquisition
cleanup risk as part of F2. Close `.stacks.log` if the later terminal-file open
fails, before installing the writer or faulthandler. This requires explicit
local ownership, not a new lifetime abstraction.

Test authoring gate: this new owner-boundary test protects retained timeout
evidence while preserving the real child pytest failure. The existing fatal
`os.abort()` case cannot catch missing output from pytest-timeout's ordinary
TerminalWriter followed by `os._exit`. The probe uses the real plugin, timer,
xdist worker and artifact files; no new product seam or mock timeout is added.
The child pytest process exit closes the producer interval before artifact and
absence assertions. Outer verification uses the normal12workers; the existing
subprocess diagnostic fixture still owns one xdist worker.

<details><summary>Verbatim CI-D1 cross-family plan review</summary>

# Review verdict on CI-D1 (retain Windows worker-timeout evidence): **PASS**, with one required wording fix before implementation (F1)

I read the live code and made no edits. The proposal's cause and effect hold up, its scope is narrow, and its tests can actually fail. F1 is a real robustness hole, but it's a one-line fix to the plan. None of the findings need a redesign.

## Premise checked against live code

- **Timer path:** In pytest-timeout (`.venv/.../pytest_timeout.py:505-542`), `timeout_timer` writes everything through `item.config.get_terminal_writer()`. A write error inside its `try` is caught and printed to stderr, so that one isn't fatal. The `finally` calls `terminal.flush()` before `os._exit(1)`. So only a failing **flush** can stop the exit, and the proposal's reason for a fail-open adapter is correct. The stack dump (`:545-558`) includes the main thread, so the named-test stack assertion will hold.
- **Timer start and settings:** With `func_only=False`, the protocol hookwrapper starts the timer before setup (`:178-193`). That matches the plan's claims.
- **Output is lost today:** execnet's `init_popen_io` (`gateway_base.py:1765-1800`) points fd1 at the null device and sets `sys.stdout` to it. `TerminalReporter` builds `_tw` from `sys.stdout` (`terminal.py:394`). So the timeout banner currently goes nowhere.
- **The stream swap is safe:** `TerminalReporter.isatty` (`terminal.py:402`) and `TerminalWriter.hasmarkup` (`terminalwriter.py:86-87`) are cached when the objects are built. `_file` is only touched by `write` and `flush` (`:168,177,187`). I found no other code in pytest or xdist that reads `_tw._file`.
- **No nested pytest runs in `tests/cli`**, so turning on the env var there doesn't spread to child test runs. Product code never reads `WEFT_TEST_DIAGNOSTICS_DIR`; only test helpers (`run_diagnostics.py`, `weft_harness.py`) do.

## Findings and suggested fixes

**F1 (required wording fix; robustness).** `UnicodeEncodeError` is a subclass of `ValueError`. As written, catching "OSError/ValueError" on write would treat a single unencodable string (for example a lone surrogate in captured subprocess output) as a dead sink. The diagnostic file would then be dropped for the rest of the session, and pytest's own escape fallback in `write_raw` would never run.
- **Fix:** open `.terminal.log` with `encoding="utf-8", errors="backslashreplace"` so encoding can't fail.
- Then the adapter only needs to catch `OSError` and the closed-file `ValueError`.

**F2 (simplification; removes a failure branch).** Open the worker terminal log in `pytest_configure`, inside the existing `try`, next to `stacks.log`, only when `workerinput` is present.
- An open failure then disables the whole plugin, the same way it does today. The existing unwritable-directory test already covers that.
- That leaves one new branch: "terminal reporter missing or incompatible at `pytest_sessionstart`." Check it with `pluginmanager.get_plugin("terminalreporter")` and `getattr(..., "_tw", None)`, not `get_terminal_writer()`, which asserts.
- Hand `config` to the plugin when it's constructed. The existing `pytest_sessionstart(self)` signature can then stay as it is.
- Extending the "Disabled/unwritable" gate to distributed mode never reaches the new code anyway, because a directory occupied by a file fails before the worker is set up. Under F2 that extension can be dropped.

**F3 (make the premise test falsifiable).** In the RED run, also assert that the Timeout banner and the unique token are **absent** from the controller's stdout and stderr. That directly tests the claim that the output is lost today.
- With the unchanged plugin, RED fails on the terminal-artifact count being 0. Say so explicitly; that counts as the "absent retained content" failure.

**F4 (make the sink-failure test precise).** The probe should close the plugin's retained file from inside the child test, using `request.config.pluginmanager.get_plugin("weft-run-diagnostics")`. That makes both write and flush hit the dead sink.
- Keep the 60s sleep and the 30s outer limit. That gap is what tells the two outcomes apart: if flush raised, `os._exit` would be skipped and the run would hit the outer 30s timeout.
- Assert exit code 1, the crash report, and no `ValueError` or `Traceback` text in stderr.

**F5 (state a side effect).** Setting `WEFT_TEST_DIAGNOSTICS_DIR` turns on the whole existing plugin, not just the new terminal log:
- Fatal faulthandler stacks move from the job log into `*.stacks.log`.
- `WeftTestHarness._attach_failure_diagnostics` (`weft_harness.py:197-206`) writes into that directory instead of a throwaway temp directory.
- Every Windows CLI process writes JSONL.

These changes keep the evidence (the artifact upload retains it) and add some. The plan should still list them, because readers of the CI log will no longer see fatal stacks inline.

**F6 (wording).** Two stale or unclear spots:
- Line 2637 says "Do not claim the 9.2.0 seam has already been read", which contradicts the root verification at line 2691. Mark it as satisfied.
- `pytest-9.2.0-source/` and the provenance JSON are not in the repository. Give their actual location or say they're kept outside the repo.

## Other checks
- **Phase records are worth keeping.** The timer stack shows which command is blocked, but only per-command elapsed times can tell "one command stuck" apart from "all nine took a combined 90s".
- **Phase records: keep them minimal.** Record begin and end inside `_run_console` (elapsed, returncode or exception type), plus harness-exit begin and end. The harness-enter record adds nothing a stack trace can't show, so it's optional.
- **Init-failure test still works after the change.** Routing init through `_run_console(console, tmp_path, "init", str(root), env=env)` keeps the existing test's `subprocess.run` monkeypatch effective. If you add `request`, that test must pass its own `request` along.
- **Restore before close is ordered correctly.** Our `pytest_sessionfinish` runs before the terminal reporter writes its summary, which then goes to the null device as it does today. The "only restore if `_file` is still our adapter" check covers a later plugin swapping the stream.
- **Workflow expression.** An expression like `${{ matrix.os == 'windows-latest' && matrix.test_slice == 'cli' && format('{0}\weft-test-diagnostics', runner.temp) || '' }}` on the existing pytest step works: an empty value disables the plugin (`run_diagnostics.py:106`) and the harness branch. `runner.temp` is already per-job.
- **Upload step.** It is correct as specified: `always()`, the same path, `if-no-files-found: warn`, and the pinned `actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9`. It can't turn a failed job into a pass.

## Primitives, correctness, elegance
Swapping one existing writer's stream, behind a single install/restore point, is the smallest primitive that keeps pytest-timeout's own output. It adds no timer, hook override, or second recorder, and nothing in the list of constraints is violated. With F1 and F2 applied, the adapter is two methods, write and flush. Nothing else is needed: the cached `isatty`/markup state means no delegation is required.

## Separate observations (not blockers)
- An optional extra check: assert the distributed passing-run `.terminal.log` contains the `PASSED` line. That would show the sink is installed on ordinary runs too.
- As the plan already says, a passing Windows rerun only shows the failure is intermittent.

</details>

#### CI-D1 implementation evidence, 2026-10-28

The opt-in worker terminal sink and installed-console phase records are implemented. The controller stream is untouched. Existing worker TerminalWriter settings are preserved, the retained file uses UTF-8/backslashreplace, and normal close restores the original stream before closing owned files. A failed retained write/flush falls back to the original stream. Partial terminal-file acquisition closes the already-open fatal-stack file. Windows CLI jobs enable the existing diagnostic directory and upload all its artifacts through the workflow's existing pinned upload action; test commands, marks, deadlines, scheduling and worker counts are unchanged.

Qualified RED (parent independently inspected): `ci-d1-timeout-red.log` failed specifically on zero retained worker terminal artifacts after proving child exit1, the crashed test identity, controller absence of banner/token, and worker-start/down events. GREEN at normal12 workers ran the eight diagnostics cases plus both installed-console tests:10passed8.16s. The original-writer mutation left the terminal artifact present but empty and failed at `assert "\\ud800" in output` with actual empty string (1failed2.39s), after the same crash/status/event guards. Restoring the implementation yielded10passed7.84s. Scoped mypy initially identified private-seam Any/None narrowing errors; explicit local annotations and reading the original stream before identity narrowing resolved them. Final diagnostics rerun after those typing-only changes:8passed2.59s. Ruff and scoped mypy (three changed Python files) pass.

Coverage includes standalone and distributed normal finish, retained real pytest-timeout banner/captured token/blocked stack (including an unpaired surrogate), fatal worker stack, disabled/unwritable diagnostic roots, partial acquisition cleanup using the actual opened stack handle, and a real timeout after closing the installed terminal sink. The latter still exits1 with the original crashed-test report and no diagnostic traceback. The existing installed-init failure ownership test remains passing. Retained phase events from the restored full run contain ten console begin/end pairs: nine successful real console calls and the injected init-failure call, plus harness enter/exit phases. No environment dictionaries are recorded.

Evidence files are under `/tmp/weft-implementation-20261027/`: `ci-d1-green.log`, `ci-d1-original-writer-mutation.log`, `ci-d1-restored-green.log`, `ci-d1-final-green.log`, `ci-d1-mypy.log`, and `ci-d1-restored-green-artifacts/`. These local results establish diagnostic behavior, not the cause of the Windows failure. Fresh-eyes and cross-family implementation review remain required before push, followed by the unchanged Windows CLI slice. A passing CI rerun establishes intermittence only. No concurrent launch workers were introduced.

CI-D1 fresh-eyes implementation dispositions: accepted F1, because reporting whole harness lifetime as cleanup duration was misleading and a failed enter must not be labeled an exit. `harness_exit_begin` now captures its own timestamp; only an actual exit start permits `harness_exit_end`, whose elapsed measures cleanup. Enter failure records `harness_enter_end` with its error instead. Accepted F2: added a real distributed subprocess regression calling the existing `record_phase` consumer before an operation and on its exception, then rethrowing unchanged. It verifies original failure status/text and retained phase order, argv, exception type and nodeid. No new recorder or framework was added.

After these corrections, the combined diagnostic and installed-console slice passed11tests6.99s at normal12 workers (`ci-d1-review-green.log`). A no-op `record_phase` mutation failed specifically at phase sequence comparison, actual `[]` versus `['operation_begin', 'operation_end']`, after the original failure status/text assertions (1failed1.48s, `ci-d1-phase-mutation.log`). The implementation was restored; the same combined slice passed11tests7.06s (`ci-d1-review-restored-green.log`). Ruff and scoped mypy remain clean. Artifacts are retained in `/tmp/weft-implementation-20261027/ci-d1-review-restored-artifacts/`. Test lane was released before this evidence update. Cross-family review and Windows CI remain open.


#### CI-D1 cross-family implementation review dispositions, 2026-10-28

The independent review returned no blocker. F1 accepted: the closed-sink probe demonstrates a write failure switching to fallback; it does not separately exercise the defensive flush exception guard. F2 accepted: normal distributed success proves the ordinary outcome, not stream restoration, which was verified by source inspection. The existing distributed arm now additionally asserts the worker terminal_sink_installed event. F3 accepted: harness_exit_begin records the body exception type, allowing comparison with the exception leaving cleanup. The partial-acquisition nit is accepted: test the constructor directly under pytest.raises(OSError), retaining the real stack handle closure assertion without risking registration into the live outer session.

Optional contextmanager extraction is declined: this slice has one consumer and explicit phase points preserve the existing lifecycle without adding another abstraction. Optional console exception-flow rewrite is declined: no current caller runs inside an outer handled exception, and the current forwarding has real failure coverage; the latent concern is recorded rather than expanding this diagnostic slice. Detail-key collisions in record_phase are outside current scope: no caller supplies reserved keys. No claim is made that arbitrary new callers are supported.

Verbatim independent review:

```text
# CI-D1 implementation review (read-only): **no blocker**

The diff is correct, scoped and fail-open. It reuses the existing owners: the `_RunDiagnostics` recorder and file lifetime, the reporter's existing `_tw`, and the workflow's pinned upload action. It adds no timer, deadline, worker-count, scheduling, product or load change. I didn't edit anything or run any tests.

## Checked against source

- **Timeout ordering.** `pytest_timeout.py:513-542` writes everything through `get_terminal_writer()`. A write error is caught at `:536-537`. Only `terminal.flush()` at `:539` can stop `os._exit(1)` at `:542`. The guards in `_RetainedOutput` (`run_diagnostics.py:32-47`) are therefore the right size: they catch only `OSError`/`ValueError`, and the original stream is never closed.
- **Writer seam.** In local pytest 9.1.1, `_file` is touched only at `terminalwriter.py:86,168,177,187`. Install (`run_diagnostics.py:94-108`) uses non-asserting lookups and runs only when a worker file exists. Restore (`:144-149`) runs before close, checks identity, and is idempotent.
- **Resource acquisition.** A failed terminal open closes the stack file (`:61-68`). A failed registration closes everything (`:182-184`).
- **Surrogate test is sensitive.** Without `backslashreplace`, the strict encoder raises inside the adapter's `write`. The adapter sets `failed`, so everything after goes to NUL, and the `"\\ud800"` assertion fails on empty content. pytest's escape fallback in `write_raw` can't rescue this, because the adapter has already absorbed the error.
- **Workflow.**
  - The expression yields `''` everywhere except Windows CLI, which leaves the plugin off.
  - The `runner` context is valid in step `env`, and the upload path matches the env value.
  - `always()` with `warn` can't turn a failed job into a pass.
  - The heartbeat writes to `sys.__stderr__` (`conftest.py:622-627`), so the swap doesn't affect it.
- **Phase forwarding.** `_run_console` keeps the same argv, cwd, env, capture and deadline. The init-failure test passes its real `request`, so the monkeypatched `subprocess.run` is still hit. Python ≥3.12 (`pyproject.toml:10`) supports `sys.exception()`. Inside a `finally` on the exceptional path it returns the exception in flight.

## Findings (none blocking)

**F1 – the closed-sink test never reaches the flush guard; the evidence overstates it.** Suggested fix: correct the wording only.
- Closing the file makes the timer's first `terminal.sep` write raise `ValueError`, which sets `failed=True`. The flush at `:539` then goes straight to the original stream.
- So a mutant that removes only the flush `try/except` (`run_diagnostics.py:41-46`) survives `test_run_diagnostics_closed_sink_preserves_worker_timeout`.
- Both guards removed would still be caught: flush escapes, `os._exit` is skipped, and the 30s outer limit fires. Removing only the write guard should be caught by `print_exc` on stderr. I didn't verify either case by mutation.
- The plan/F4 sentence "both write and flush reach the closed sink" is false. Keep the flush guard as defensive code. A flush-only failure can't be triggered reliably with real I/O, because line buffering flushes on the final `sep` newline. No new test is needed.

**F2 – no test can detect missing normal restoration; the plan overstates this too.** Suggested fix: correct the wording, plus an optional assertion.
- If restoration were removed, writes after close would hit the closed adapter file and silently fall back to the same original stream. The distributed normal-close test can't tell the difference.
- This follows from the fail-open design: restoration is hygiene that can't be seen from outside. Don't cite that run as restoration evidence.
- Optional cheap addition through the public JSONL owner: in the `distributed=True` arm, assert that gw0 recorded a `terminal_sink_installed` event. That proves the sink is installed on ordinary runs.

**F3 – `harness_exit_end.error` mixes up body failure and cleanup failure.** Optional.
- At `test_cli_run_installed_entrypoint.py:271-278`, `sys.exception()` reports anything leaving the `with`, including a body `AssertionError` followed by successful cleanup. It's the same kind of mislabel that fresh-eyes F1 fixed for enter.
- Smallest fix: also record `error=` from `sys.exception()` at `harness_exit_begin` (`:268-269`), so a reader can compare the two.
- This doesn't help the target 90s case, where `os._exit` skips both end records.

## Elegance (optional, owner's call)

- **Re-indent churn in the CLI test.** The two nested `try/finally` blocks with the `exit_started` sentinel force about 130 lines of re-indent. Moving exactly that logic into a local `@contextlib.contextmanager` named `_recorded_harness(harness, config)` would leave the test body at its original indent, with a one-line `with` change. `contextmanager` already forwards thrown exceptions and keeps `__exit__` suppression semantics. This doesn't change the lifecycle.
- **`_run_console` (`:36-59`).** `try: ... except BaseException as error: record; raise`, followed by a success record, would drop both the `result` sentinel and `sys.exception()`. It also avoids a latent mislabel: in a `finally` on the success path, `sys.exception()` returns any outer *handled* exception. No current caller runs inside an `except`, so this is latent only.

## Nits

- **`record_phase` keys (`run_diagnostics.py:188-197`).** Passing `phase`, `nodeid` or `event` as detail keys would raise `TypeError` at the call, before `_record`'s guard. No caller does this today.
- **Partial-acquisition test (`test_run_diagnostics.py:197-219`).** It drives module `pytest_configure` with the live outer `pytestconfig`. If a regression ever let construction succeed, it would register a second plugin into the outer session. Calling `_RunDiagnostics(tmp_path, "probe", pytestconfig)` under `pytest.raises(OSError)` is narrower. As written, the test does catch removal of the cleanup (`opened[0].closed`).

## Test oracles

- **Timeout probe: strong.** It uses the real timer, xdist worker, TerminalWriter and files, and checks absence on the controller and content in the artifact. The RED counts.
- **Phase test: real subprocess, catches the no-op mutant.** As the plan accepted, it covers the `record_phase` seam, not the `_run_console`/harness wrappers. The next Windows run supplies that evidence.

## Pre-existing observations (no action)

`_record` reopens the JSONL file for every event. The harness failure snapshots now land in the uploaded directory, which is the accepted plan F5. Neither gets worse with this diff.

On cause, I agree with the plan: the retained banner, stack and unmatched phase from the unchanged Windows CLI job should decide the next repair. A passing rerun only shows the failure is intermittent.
```

Final verification after cross-family dispositions: the unchanged selected diagnostics plus installed-console slice passed11tests6.56s at normal12 workers (`/tmp/weft-implementation-20261027/ci-d1-crossfamily-final-green.log`). Scoped Ruff, format check and mypy all pass for the three changed Python files. The test lane was released before recording this result. Both implementation review passes are complete with the explicit dispositions above; unchanged Windows CI remains the external diagnostic gate, and no cause of the original Windows failure has been claimed.

Root closure verification: the final fresh-eyes pass confirmed every accepted cross-family disposition in the final source. Root independently inspected the qualified RED and both mutation failure logs, final11-pass result, and retained real phase events. Whole-repository Ruff/format (632files), full mypy (479files),164policy tests, documentation paths and DOM-15 fixture checks passed before the final small review dispositions; scoped Ruff/format/mypy and the full11-case slice passed again afterward. CI-D1 is ready for Windows evidence collection; it does not resolve the original failure cause.
