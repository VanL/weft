# Runtime Ownership and Test Evidence Repairs

Status: completed
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
| [RM-5.2] | Bound delivery of already-captured output by the private drain budget | Bounded further capture, then full delivery of the frozen captured backlog; Consumer seals missing logical finals on success | Integrated review reproduced captured-data loss at 128 MiB under ordinary Consumer backpressure | Owner-approved revision promoted 2026-10-28 (see the execution record) |

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
compaction measurements and final review were not performed under the planning
task; they are recorded in the Implementation Execution Record below.

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

### Record condensation note (2026-10-29)

This record was condensed on 2026-10-29 under `docs/agent-context/runbooks/writing-plans.md` §8 by `2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md` Task 4. Reviewer metadata, findings and dispositions, verification command summaries, decisions and measurement tables are kept; verbatim reviewer transcripts and repeated raw pastes were removed. The uncondensed text is recoverable with `git show 540707a3:docs/plans/2026-10-27-runtime-and-test-repair-plan.md`. Paths under `/tmp/weft-implementation-20261027/` are disposable local evidence, not required inputs.

Reviewer metadata for every entry below unless stated otherwise: author family OpenAI; Tier-1 cross-family reviewer Anthropic Claude Code 2.1.293 / claude-opus-5-5 (Opus 5.5) through the contained read-only invocation recorded above (`--permission-mode plan`, restricted Read/Grep/Glob, no session persistence, bounded subprocess timeout; later rounds add `--output-format stream-json --verbose --include-partial-messages --debug-file` telemetry and require a final `subtype=success` result). Reviewers could not read `/tmp` evidence under containment, ran no tests and made no edits, so execution results are the author's or parent's verified evidence. "Fresh-eyes" entries are same-family passes and are not cross-family approval.

### Authorization, comprehension and promotion

The owner authorized implementation of this repair plan followed by admission measurements, with concurrency deferred and review gates retained, then answered "Implement locally. You will monitor CI which has windows runners": local implementation proceeds after local probes plus primary Windows/Python source verification, with Windows CI as required acceptance. Branch `codex/runtime-repairs-dispatch-measurements`; CI runs through the pull-request workflow (PR #48). Test execution is serialized against shared-tree edits; disjoint heap and test-repair work may proceed alongside runtime research.

Comprehension answers before production edits: (1) descendants retain independent pipe writers after root exit; bound drainage, preserve the observed root outcome. (2) a buffered writer can hold the lock needed by cross-thread close; the cancellable adapter owns close and the supervisor joins it. (3) failed monitor acquisition still leaves the runner owning its already-spawned child, so rollback precedes propagation. (4) leftist rank bounds the right spine only; arbitrary ancestor repair has no promised logarithmic bound, hence local geometric reclamation. (5) forbidden pairs exercise the validator; literal edge expectations prove action/ID/target, while TaskSpec owns idempotence/terminal self-transition rules.

Task 1 spec promotion: the three reviewed insertions were applied to the two governing files against `e220748f786ea72dad00fbc7672fc484e24bef0c` (diff SHA-256 `6f17fc6d2a814ebc6ee946c9995aa7aa38fccf3e63e058f9a3ba23b2356f42c1`) before runtime edits.

### Initial failing controls and test-repair implementation (T1–T4)

- S1 RED: four production-reducer regressions failed at the intended assertion (1.15 s): continuing sibling churn retained 3 nodes at L=1; late recovery retained `[999, 202, 201, 200]` instead of `[999]`; candidate pruning returned cross-owned 201 instead of 200; same-ID re-addition retained duplicate nodes.
- R1–R3/F1 RED: seven core cases failed against unchanged runtime code (7.63 s): timeout/cancel blocked in input, silent and continuous inherited-pipe writers prevented root return, failed monitor acquisition left the root alive, and a replenishing real queue prevented the inner drain from yielding. After correcting fixture setup, both reserved KEEP/CLEAR Consumer cases failed at "failed Consumer left its owned command alive" (1.12 s).
- T1–T4: the retained literal state-pair oracle invokes the real validator, and its 14-edge table checks literal action/ID/target, so only the duplicate core allowed-edge table was removed. TaskSpec tests cover three forbidden created edges, nonterminal idempotence and all five terminal self-transitions. Executable preflight observes actual resolver output with configured and default fixture executables. Three command deadline tests replace the module-local time binding.
- Sensitivity: isolated in-memory production mutations produced 44 intended failures (each of the three created→completed/timeout/killed bypasses fails both the matrix and TaskSpec assertion, 6 failures; wrong spawning→failed action; each of 14 edge removals against both keepers; five terminal self-transition bypasses; three nonterminal idempotence breaks; ignored project settings). The real SQLiteRunner transaction held across its 50 ms waiter timeout: the old shared-clock patch stalled past 250 ms, the repaired test let the real OperationalError deadline fire.
- Focused GREEN: the six-file Task 4 command passed 196 tests (6.76 s) at default concurrency.

T1–T4 review: Claude, 172.36 s, **no blocker**. F1 (nit): the `created` snapshot was taken after a same-status call; accepted with snapshot-before-first-call and a stable-field-rewrite control. F2 (nit): the 44-failure total counts the six matrix/TaskSpec forbidden-edge failures twice; accepted by counting them explicitly. Focused re-review, 46.50 s: **PASS**, F1/F2 closed; the stable-field-rewrite control survived the old test and failed the repaired one; the focused file passed 17 tests (1.19 s).

### Runtime primitive and local implementation evidence

The owned adapters use raw nonblocking pipes, finite reads/writes, stop-event waits only on would-block, and owner-thread close; they are registered before start and joined on every exit. `io.IncrementalNewlineDecoder` preserves standard newline/codec behavior (no custom CR parser). The execution clock starts before input delivery; root exit starts bounded final drainage independently of inherited writers. Runtime stop/kill hooks and the primary exception survive rollback. (This `_PipeAdapter` design was later replaced; see Closure.)

- Disposable real-pipe probes (Python 3.14.9, macOS 27.0.1 arm64): EAGAIN after 65,536 bytes of a 1 MiB blocked input; supervision ran 18 turns in 0.207 s; adapter stop/join about 0.1 ms; a silent inherited writer drained for 0.250877 s; continuous output drained for 0.2511 s with the descendant still alive; large Unicode input and 25 split UTF-8/newline/error cases matched standard decoding. macOS results only.
- Primary portability sources: Python 3.12 `os.set_blocking` documents Windows pipe support; CPython 3.12.0 `fileutils.c` implements PIPE_NOWAIT and would-block translation; CPython `subprocess` retains synchronous Windows input writing in `communicate`, so it does not supply this cancellation boundary.
- Local runtime GREEN: 276 tests (33.60 s) across the subprocess runner, TaskRunner, Consumer terminal events and command-runner parity files; Docker caller tests 87 (0.29 s). Isolated controls (blocked-input path restored, drain bounds/deadlines removed, adapters registered after start, primary exception masked, decoder state reset, final flush skipped, wrong codec, acquisition rollback skipped, adapter errors ignored) each failed its intended assertion.
- Real Docker proof through the production plugin/runner/shared supervisor (`python:3.12-bookworm`, network disabled, no mounts): 500,000-character Unicode roundtrip plus stderr SHA-256 in 0.593 s; a 32 MiB write to a nonreader reached EAGAIN and returned timeout in 0.606 s for a 0.3 s limit; cancellation after EAGAIN returned in 2.499 s through the existing Docker stop hook; all owned containers removed.
- Integrated local checks: full SQLite/slow 5,950 passed, 49 skipped, 2 failed (196.57 s); both failures were stale literal Ruff inventory expectations (removed decoder group 037, added adapter-failure group `RUFF-SUP-354`, directives 327→326, raw C901 115→113), updated explicitly; 44 Ruff-policy tests then passed. Full mypy (478 files), Ruff, format and registry passed. Local extensions: Django 163 passed/1 skip, Docker 113, macOS sandbox 26, Microsandbox 81 passed/4 deselected.

### S1 retention and Manager latency evidence

The reducer tracks physical/live counts, reclaims only its local heap when H > 2L, and checks both pending membership and current owner-handle identity; pairwise leftist meld preserves meldability; local deduplication handles same-ID refill; owner resolution/path compression is counted separately. All 44 reducer/Manager/system tests passed (16.03 s); four isolated mutants (no reclamation, membership-only rebuilding, membership-only pruning, retained duplicates) failed the intended assertions.

Alternating baseline/repaired measurements, L = 100/1,000/10,000, unrelated backlog 0/10,000, three repetitions per cell (36 runs), milliseconds:

| Live entries | Unrelated entries | Baseline loop ms | Repaired loop ms | Repaired largest compaction per repetition ms |
|---:|---:|---|---|---|
| 100 | 0 | 1.094, 1.089, 1.112 | 1.330, 1.276, 1.320 | 0.067, 0.060, 0.054 |
| 100 | 10000 | 1.055, 1.123, 1.114 | 1.396, 1.317, 1.400 | 0.072, 0.061, 0.065 |
| 1000 | 0 | 12.947, 12.839, 12.787 | 14.304, 14.518, 14.899 | 0.640, 0.583, 0.604 |
| 1000 | 10000 | 12.895, 12.840, 12.799 | 14.847, 14.569, 14.399 | 0.547, 0.559, 0.557 |
| 10000 | 0 | 145.632, 146.510, 149.045 | 168.429, 168.645, 164.205 | 5.694, 6.037, 5.629 |
| 10000 | 10000 | 145.780, 147.573, 148.629 | 167.736, 170.045, 169.485 | 5.885, 5.664, 5.766 |

At L = 10,000 each reclamation visited 19,999 local nodes with 22,893 meld calls, independent of unrelated backlog; retained nodes peaked at 19,999 instead of 40,000 after 30,000 churn cycles. This repairs a memory bound at CPU cost. The 169 versus 147 ms totals include asymmetric observer costs and must not be read as a 15% production slowdown. Additional L = 10,000 / depth-64 contracted-owner measurement: H = 20,001 rebuilt to 10,000 in 7.106 ms, 20,001 heap visits, 10,000 live-owner resolutions, 10,063 owner edges (64 + 9,999 after compression), 22,894 meld calls (measured under PostgreSQL load).

Staged real-Manager gate (actual broker classification, exact competing reservations, production stale-hint removal, normal `process_once`; a keyed PING queued immediately before compaction): L = 100 compacted H = 201 to 100 in 0.107 ms, PONG in 3.004 ms; L = 10,000 compacted H = 20,001 to 10,000 in 5.422 ms, PONG in 7.434 ms, in the same turn; all six owned PIDs absent after cleanup. This is staged integration, not a natural 20,000-process workload or a universal latency bound; larger buckets retain the disclosed linear pause risk. A first probe that registered its in-process Manager for harness termination exited by SIGTERM and is retained as failed evidence.

### Runtime and S1 implementation reviews

| Round | Reviewer / duration | Verdict | Findings and dispositions |
|---|---|---|---|
| Runtime implementation | Claude, 341.61 s | Blocker F1; F2 evidence-gated | F1 [P1]: the 16-chunk cap plus sleeps while chunks remained queued throttled reads, so successful commands could lose output; reproduced (32-byte reads: root wrote 100,000 bytes, runner returned ok with 12,288 characters, no EOF callback); accepted: sleep only when both output queues are empty. F2 [P2]: Windows `OSError(EINVAL)` on closed stdin; real early-exit/8 MiB probe observed BrokenPipe on macOS; accepted with EINVAL accepted only on Windows writes (CPython 3.12 `subprocess.py` ignores EINVAL for closed stdin; `fileutils.c` halves Windows ENOSPC writes, so the 64 KiB all-or-nothing concern does not match the primitive). F3 [P3]: ambient `sys.exception()` in `finally`; reproduced; accepted with a function-local failure flag. F4 [nit] dead-branch claim declined: the timeout path's second poll can observe a newly exited root. |
| S1 implementation | Claude, 308.84 s | **No blocker** | F1 accept (deep owner-chain measurement, above); F2 accept (three isolated mutants for the two newer tests); F3 accept (exact commands/hashes/static evidence); F4/F5 accept (remove duplicate owner resolution; exact probe cardinality). |
| Runtime focused re-review | Claude, 238.62 s (a first dispatch with an incomplete brief was stopped and not counted) | **No blocker**; F1–F3 correct | R2-1 accept: Windows EIO added to the explicit input/error matrix (platform-only suppression mutant now fails "DID NOT RAISE OSError"); R2-2 accept: single-consumer rationale comment; R2-3 accept: precise scenario IDs. |
| S1 focused closure | Claude, 127.76 s | **No blocker**; F1–F5 closed; Manager latency evidence accepted within stated limits | N1: wording correction only (deduplication avoids repeated resolution for retained IDs; cross-owned stale duplicates still resolve per node within the H-based accounting). |
| Runtime round 3 | Claude, 45.56 s | **PASS** for R2-1/R2-2/R2-3 | R3-1 test-name nit declined. |

Runtime review corrections: RED four intended failures and two passes (4.90 s); then the four-file runtime gate passed 282 tests (34.11 s), Docker caller tests 87; reverting the main-loop or final-drain sleep guard, removing BrokenPipe handling, or broadening EINVAL to all platforms each failed its intended assertion. Docker smoke repeated: roundtrip 0.597 s, timeout 0.558 s, cancel 2.306 s.

Owner review requirement (2026-10-27): before completion, perform both a fresh-eyes implementation review and a separate other-model/cross-family review on the final combined delta. Both must explicitly answer: Is it correct? Is it elegant enough for an expert systems programmer? Does it use the underlying data structures properly or overcomplicate them? Verify findings against actual code, firing tests, theory and measurement evidence; retain dispositions and focused re-review of accepted fixes. Earlier slice reviews do not replace these final passes.

Verification after corrections (2026-10-28, after the native implementation agent's usage-limit interruption and owner-authorized resumption): pre-correction full PostgreSQL 5,903 passed, 27 skipped (345.38 s, `bin/pytest-pg --all`); corrected full SQLite/slow 5,958 passed, 49 skipped (202.89 s); corrected full PostgreSQL 5,909 passed, 27 skipped (341.05 s); full mypy (478 files), Ruff, format and registry passed; S1 final focused gate 24 passed (1.97 s) with seven final isolated controls failing (17 parameter cases).

Author fresh-eyes integration pass (2026-10-28): correctness — the post-spawn scope owns all acquired resources, stops all adapters before joining, preserves the primary failure; cancellation remains ahead of root/timeout decisions; heap counts transfer through meld and stay bounded; no queue reservation, TaskSpec transition, public API or admission authority changed. Elegance — one acquisition/rollback owner and one small pipe adapter, standard incremental codecs. Counterarguments retained: portable nonblocking adapters poll idle pipes at the 10 ms floor; local compaction has an O(L) pause; arbitrary callbacks cannot be preempted; unbounded captured-output queues predate the repair.

### Integrated repair review: drain semantics reconsideration and capture-boundary revision (2026-10-28)

Integrated review (`e220748f` → `4c490043`): Claude, 411.21 s, **blocker F1**, with F2 requesting owner disposition.

- F1 [P1]: after root exit, one 0.25 s budget stopped delivering already-captured output from the unbounded reader queues. Confirmed through the actual Consumer, its 256-slot worker-result queue and SQLite broker, without artificial callback sleeps: the 32 MiB control completed intact; at 128 MiB the repaired supervisor captured all 134,217,728 characters but returned only 81,330,176, with 808 queue items (including the genuine EOF marker) remaining, and the public result was `completed` with `value=None`. The exact `e220748f` supervisor delivered all 134,217,728 characters. An uninstrumented 128 MiB run returned only 70,385,664 characters, ruling out observer wrappers. Evidence: `runtime-integrated-stream-flood-current-134217728.json`, `runtime-integrated-stream-flood-baseline-134217728.json`. This 128 MiB Consumer-and-broker reproduction is the integration evidence; the 128 KiB controlled-sink payload in `test_terminal_capture_delivers_backlog_after_capture_budget` is the mutation proof.
- F2 [P2]: with 14 characters of root output and a silent inherited-pipe descendant, all data was delivered but no final frame was published and the public completed result had `value=None` (`runtime-integrated-stream-inherited-current.json`). Physical EOF and logical output finalization must stay distinct; the governing completion-order sentence is [MF-3], and [MF-5] does not authorize a reader to invent an end frame.

**RECONSIDERATION REQUESTED** (the fix changes the promoted [RM-5.2] rule). Proposed and then **owner-approved** (owner: "Ok, this sounds reasonable"): bound further pipe capture by the private drain budget, stop and join readers to freeze the finite captured backlog, then deliver that backlog fully; Consumer finalizes the logical stream (stderr before stdout, success outcomes only) before publishing completion, without manufacturing physical EOF. Explicit tradeoff: large captured output or slow sinks can extend delivery beyond 250 ms; output produced after capture is stopped is not promised. The operative spec delta and Task 2 instructions were updated with this promotion.

| Round | Reviewer / duration | Verdict | Findings and dispositions |
|---|---|---|---|
| Approved correction, pre-implementation | Claude, 313.18 s | **No blocker** | C1/C2/C4 accepted into Task 2 (finalized adapter-error check before delivery; frozen loop ends on queue emptiness; returned-outcome callback invariant). C3 accepted: controlled sink is mutation proof, 128 MiB is integration evidence with exact length/hash. C5 symbol reconciliation after implementation. C6 missing-final ordering stderr before stdout. C7 satisfied by the terminal-path parameter table. |
| Completed-work | Claude, 292.03 s | **No blocker**; all three owner questions yes | CB1 accept (remove unused private timeout parameter); CB2 accept (late-callback failure test asserts zero calls after the first exception; a retrying mutant passes the old keeper and fails the new one); CB3 accept ([MF-3] states stderr-before-stdout when both finals are missing); CB4 decline (flat guards are clear). |
| Final scoped closure | Claude, 72.83 s | **PASS** for CB1–CB3 | The parent verified the isolated retry mutant's source. |

Capture correction implementation: `_finish_streams` uses the existing reader thread joins with one absolute deadline, a shared `_stop_and_join_pipe_adapters`, a finalized adapter-error check before callbacks, and exhaustive alternating finite-queue delivery; the obsolete per-chunk final-delivery deadline and its superseded test were removed; Consumer reuses its callbacks for success-only missing logical finals. RED: 10 cases, 8 intended failures, 2 controls pass (1.59 s); the timeout row made readiness-gated with an owner-local clock facade still failed exact-output equality (2.04 s). Focused GREEN: 662 passed (32.26 s) across runner, Consumer, parity, task-execution and result-command tests; Docker 87 passed; mypy (478 files), Ruff, format (631 files), suppression inventory clean. Six isolated mutation controls fired (discarded frozen backlog; removed early adapter-error check; removed logical seals; reversed seals; sealed failed outcome; skipped data callbacks). Real Consumer/broker: corrected 128 MiB runner/direct/public values all contain 134,217,728 characters with SHA-256 `8fb4c93e9cdd636cfe06929df7ec7cc521fe3fd3affdfe81fc13d8fcb737d26a`, matching the baseline; post-terminal finalization 0.5442435 s (capture/join plus delivery); the inherited-pipe case kept physical EOF false on both streams, delivered 14 characters, emitted one logical final per stream and returned the 13-character stripped public value. CB1/CB2 GREEN: `./.venv/bin/python -m pytest tests/core/test_subprocess_runner.py::test_terminal_capture_delivers_backlog_after_capture_budget tests/core/test_subprocess_runner.py::test_late_captured_output_callback_failure_preserves_exception_and_cleanup tests/core/test_subprocess_runner.py::test_capture_join_reports_adapter_error_before_delivering_callbacks tests/core/test_subprocess_runner.py::test_root_completion_bounds_inherited_pipe_drain --tb=short`, 10 passed (3.28 s).

CI at `4c490043` (run `40699715283`): attempt 1, 31 checks succeeded, 1 failed, 5 skipped; the failure was Windows/Python 3.14 `test_manager_autostart_ensure_restarts_after_abrupt_child_kill`, whose first autostart-spawn observation expired before the kill/restart stage (job `122856145718`). Bounded investigation: the fixture is a function target that does not enter the command-pipe supervisor, and Manager and the test are unchanged from `e220748f`; neither rules out shared load. One unchanged same-head failed-job rerun (attempt 2) completed successfully, including all four dependent jobs. That establishes intermittence, not the cause. No timeout or worker-count change.

### S2: retire a serviced terminal-publication retry (2026-10-28)

Admission pre-B clarification review (Claude, 375.72 s) **BLOCKED** on B-F1: an expired publication retry remains selected after broker recovery begins fresh proof grace. B-F2–B-F6 (P3) dispositions are recorded in the dispatch plan. Probe closure: baseline timeout projection `[0.0]` at recovery; the reset control `[15.0]`, then `[14.0]` one controlled second later; both preserve the exact reserved row through proof grace, dispatch a real second request, then commit the real failsafe; the reset does not clear a future retry. Two earlier probes exited 143 during setup/cleanup and are not acceptance. Round-2 plan review (Claude, 388.96 s): **PASS** for the revised plan; B-F2–B-F6 verified; R2-F1 accept (Source specs and strategy-A delta name the normative [MA-1.6a] clarification); R2-F2/R2-F3 accepted in the admission plan; R2-F4 accept (all three child deadline families advance or retire when serviced).

Committed S2 RED: one failure (1.85 s, 12 workers) at `timeouts and min(timeouts) > 0`, seeing `[0.0]`, after real clean exit, both named broker failures, future-retry retention and fresh-grace start had passed. Implementation: `_cleanup_children` clears `terminal_retry_after_ns` after the future-retry guard and before proof grace; the failure path still re-arms it. Seven focused cases passed on SQLite (2.23 s) and the canonical PG slice (7.56 s). Controls: removing the reset restores `[0.0]`; moving it before the future guard fails the preserved-deadline assertion.

S2 completed-work review: Claude Opus 5.5, 395.43 s, **no blocker**, unit `784d6b7aad893c77bd69cf9323db0d0a185bda2c` against `4c49004320bb281a6557cd80072356dae225d047`.

| Finding | Disposition | Rationale, evidence, and required action |
|---|---|---|
| S2-F1 [P2, inference] | Accept as a residual test risk; retain the witness for imminent B | The witness assumes one-turn PG admission without owning global server headroom, and its frozen Manager `time_ns` would prevent a denied admission retry from expiring. Focused and full standard-load PG runs passed, establishing non-occurrence in those runs only. If it fails at its source-retention or launch assertion, stop corrective reruns under [TS-0]/[ALT-WT-017], preserve load and admission state, and distinguish legitimate pressure denial from a product or clock-fixture defect before changing the oracle. Do not reduce workers, bypass admission, invent capacity or enlarge a timeout. |
| S2-F2 [P3] | Accept the coverage distinction; retain the witness for B | The unrepaired owner also dispatched the second request, so the witness is not credited with S2 sensitivity; it is a first-dispatch/retained-custody baseline for B's child-deadline gate. |
| S2-F3 [nit] | Decline optional exact-list assertion | Positive bounded timeout, exact grace start and zero consumed retry express the contract; both reset controls fire. |
| S2-F4 [nit] | Accept the equivalence-owner analysis; no additional case now | Initial failure exercises the same unconditional re-arm; add a distinct fault control only if that owner is split. |

Distinct author fresh-eyes S2 review: **no production blocker**; the repair consumes a one-shot timer at its existing service point, reuses `ManagedChild.terminal_retry_after_ns == 0` as the unarmed state, adds no index, cache, queue, worker or polling cap, and restores [MA-1.6a] without changing proof classification, grace length, admission or dispatch.

Acceptance after S2 at head `784d6b7a`: local standard-load SQLite 5,970 passed, 49 skipped (196.44 s); PostgreSQL 5,921 passed, 27 skipped (388.33 s); mypy (478 files), Ruff, format (506 files), registry and six documentation tests passed. CI run `40715396491` attempt 1 failed: all Windows/POSIX core groups passed, but the PostgreSQL remaining shard failed `tests/tasks/test_runner.py::test_command_session_terminate_kills_descendants` while waiting for `interactive-child.pid`, and the four dependent jobs were skipped. Attempt 2 at the same head completed **success**, including all four dependent jobs. That establishes intermittence, not cause.

### T5: CommandSession fixture ownership (2026-10-28)

The old fixture left the live root, descendant and three reader/exit threads after the injected readiness assertion. The candidate preserves the exact exception object, adds status/stderr context, starts cleanup ownership immediately after session acquisition, and leaves no live owned process or thread; a no-op `CommandSession.terminate` still fails at `assert _wait_for_pid_exit(child_pid)`. Both generated-script consumers passed on SQLite (2 passed, 4.01 s) and PostgreSQL (2 passed, 5.33 s). Final file SHA-256 `401b5376991f80f2a0e664f8c6bd0b25c54571af4bada7d94f0cbe6e5b6c9012`. Focused commands:

```bash
./.venv/bin/python -m pytest tests/tasks/test_runner.py::test_command_session_terminate_kills_descendants tests/tasks/test_runner.py::test_task_runner_timeout_terminates_command_descendants --tb=short
./.venv/bin/python bin/pytest-pg --all tests/tasks/test_runner.py::test_command_session_terminate_kills_descendants tests/tasks/test_runner.py::test_task_runner_timeout_terminates_command_descendants
./.venv/bin/mypy tests/tasks/test_runner.py --config-file pyproject.toml
./.venv/bin/ruff check tests/tasks/test_runner.py
./.venv/bin/ruff format --check tests/tasks/test_runner.py
```

The interactive `start_session` path does not call the repaired one-shot supervisor, which narrows but does not prove the CI cause. T5 review: Claude, 250.59 s, **no blocker**. F1 [P3] accepted as provenance clarification (the probed candidate differs only by Ruff wrapping; full-module ASTs verified equal). F2 [nit] vestigial `importorskip` left unchanged. Observations O1–O7 (sibling bare-PID kill, PID-reuse oracle, unclosed session pipes, partial pidfile read, unresolved CI cause) recorded, no runtime repair implied.

### T6: Consumer monitor-validation rollback ownership (2026-10-28)

Run `40721270954` at `40f094c34e3a598ee8c778f5bda53be72aea54aa`, Windows 3.12 remaining job `122932890976`, failed the KEEP parameter of the R3 Consumer regression: expected `BaseResourceMonitor subclass`, observed PermissionError while accessing the test's `command.pid` marker (1 failed, 2,167 passed, 75 skipped). A controlled read fault reproduced the masked validation assertion in both reserved-policy cases (dependency sensitivity, not a Windows syscall diagnosis). Refinement: retain the already-owned `Popen` immediately instead of reconstructing a psutil identity, and assert its recorded returncode; a poll-only oracle falsely passed both kill-without-reap controls, while the final oracle fails missing rollback and kill-without-reap for both policies. Final source SHA-256 `85ede730a503fbc48f0faec64c2cb36e06f992e53bf8ca2854a6e77c06d12bcf`; affected file 10 passed (6.60 s); canonical PostgreSQL KEEP/CLEAR pair passed (1.93 s).

| Round | Reviewer / duration | Verdict | Dispositions |
|---|---|---|---|
| First T6 review | Claude, 275.35 s | **No blocker** | F1 accepted through removal (retain the acquired Popen before assertions); O1 accepted proof limit (acquired root only, not Windows venv redirector descendants); O2 eliminated. |
| Final focused review | Claude, 174.31 s | **No blocker**; no findings in scope | O1: investigate any bounded rollback expiry on Windows, do not relax the oracle; O2 ownership semantics recorded; O3/O4 pre-existing cleanup observations; O5 descendant coverage limits. |

Windows confirmation: at `8261a4b09f3dd2dcb92af02d3f5b926ab9efe10d`, run `40730441608`, Windows 3.12 remaining job `122987338501` passed, including the repaired regression. It does not establish the original marker-access failure's OS cause.

### T7: quiet-output notification oracle (2026-10-28)

Head `63dd4752fdff1e3a1da42187d881d0f1942565ce`, run `40743696948`, PostgreSQL remaining job `123012269327` failed `tests/tasks/test_task_interactive.py::test_production_driver_delivers_quiet_output_only_through_notify[muted]` (`assert delivered() is notify`, True versus False); all 27 OS/Python jobs, the other two PG jobs, lint and coverage passed; the four dependent jobs were skipped. Governing behavior: [CC-2.2.1], [IMPL.8]–[IMPL.11], [TS-0]. Diagnosis: the barrier announced quiet waiting at entry to `_wait_for_activity_body`, before the retained strategy consumed the local wake armed by initial stdin delivery, so a captured frame could be delivered on that legitimate follow-up turn. A controlled original-order probe reproduced the exact bad oracle on both backends (SQLite 1.42 s, PostgreSQL 2.10 s). Correction: observe the hint-free strategy wait after session readiness, then release the child; muted absence starts only after the real capture callback; the 5 s delivery budget and 0.5 s muted interval are unchanged. Interactive module GREEN: SQLite 30 passed (3.34 s), PostgreSQL 30 passed (5.52 s); an isolated Consumer callback-disconnection mutant failed the intended assertion (6.52 s). Review: Claude, 214.78 s, **no blocker**; F1 accept (causal-ordering comment at the edit point); F2 reject expansion (no stream-specific observer for a producer that never writes stderr).

Original-load verification: full SQLite 5,982 passed, 38 skipped (232.37 s); the first full PostgreSQL run stopped at 4,751 passed, 29 skipped on the admission startup-pressure setup (documented in the dispatch plan); after that fixture correction, full PostgreSQL 5,934 passed, 29 skipped (338.95 s).

Corrective-head CI at `a385f3e86d620f3dcf783e535a22c0f221394382`, run `40769090681`: all three PostgreSQL jobs, 26 of 27 OS/Python jobs, lint and coverage passed; the original notification test passed. Windows 3.14 CLI job `123101422111` failed because xdist worker gw2 exited during `test_installed_console_function_handoffs_from_fresh_project` (292 passed, 10 skipped, one worker-loss failure) about 90.19 s after its start, consistent with the module's 90 s timeout marker but with no captured timer event or stack (pytest-timeout output goes to the worker terminal writer, which execnet points at the null device). Dependent jobs skipped. No timeout or load relaxation; a diagnostic-only slice (CI-D1) was required.

### CI-D1: retain Windows worker-timeout evidence (2026-10-28)

Class 4, test/CI observability; governing contract [TS-0]; no normative behavior change. Outcome: retain the existing pytest-timeout worker output in a persistent artifact so the next Windows 3.14 CLI failure identifies the blocked stack/phase, preserving test outcome, the 90 s item timeout, the workflow 900 s default, full CLI selection, worker-count computation, xdist scheduling, command deadlines and cleanup. Design: `tests/helpers/run_diagnostics.py::_RunDiagnostics` (opt-in through `WEFT_TEST_DIAGNOSTICS_DIR`) opens `<worker>-<pid>.terminal.log` (UTF-8, `errors="backslashreplace"`, line-buffered) only for xdist workers and swaps the existing reporter writer's `_file` for a fail-open write/flush adapter (on OSError/ValueError it falls back to the original stream, which is never closed), restoring the original stream before closing owned files; partial acquisition closes already-opened files. The installed-console acceptance test records phase events (console begin/end, harness enter/exit) through the existing recorder. `.github/workflows/test.yml` enables the directory for Windows CLI jobs and always uploads it with the existing pinned upload action. Source prerequisite: the exact PyPI pytest 9.2.0 wheel (SHA-256 `07acdcf88ec174f907dc01af70bf52d49b014bbab03faeeade31c96b18c2e588`) has the same reporter `_tw` / writer `_file` seam as local pytest 9.1.1. Interpretation rule: a captured Timeout banner at 90 s confirms the termination mechanism; the stack plus unmatched phase decides the next repair; a passing rerun records intermittence only.

| Round | Reviewer / duration | Verdict | Dispositions |
|---|---|---|---|
| Plan review | Claude | **PASS** | F1 accept (`errors="backslashreplace"`); F2 accept (open in the configure scope with explicit acquisition ownership, non-asserting reporter lookup); F3 accept (RED proves exit 1 and controller absence of banner/token, then fails on zero retained artifacts); F4 accept (real closed-sink probe; 60 s block versus 30 s outer limit detects a flush that prevents exit); F5 accept (all opt-in effects named: fatal stacks, harness snapshots, JSONL move into the uploaded directory); F6 accept (pytest 9.2.0 verification marked satisfied). Fresh-eyes added partial-acquisition cleanup under F2. |
| Fresh-eyes implementation | same-family | — | F1 accept (`harness_exit_begin` has its own timestamp; enter failure records `harness_enter_end`); F2 accept (real distributed `record_phase` regression). |
| Cross-family implementation | Claude | **No blocker** | F1 accept (the closed-sink probe exercises the write guard, not the flush guard; wording corrected); F2 accept (normal distributed success does not prove restoration; the distributed arm now asserts the `terminal_sink_installed` event); F3 accept (`harness_exit_begin` records the body exception type); partial-acquisition nit accept (constructor tested directly under `pytest.raises(OSError)`). Optional context-manager extraction and console exception-flow rewrite declined; `record_phase` reserved-key collisions out of scope. |

Evidence: qualified RED failed specifically on zero retained worker terminal artifacts after proving child exit 1, the crashed test identity, controller absence of banner/token and worker start/down events. GREEN at 12 workers: 10 passed (8.16 s). The original-writer mutation left the artifact empty and failed at `assert "\\ud800" in output` (1 failed, 2.39 s); restored 10 passed (7.84 s). After fresh-eyes corrections: 11 passed (6.99 s); a no-op `record_phase` mutation failed at the phase-sequence comparison (`[]` versus `['operation_begin', 'operation_end']`); restored 11 passed (7.06 s); after cross-family dispositions 11 passed (6.56 s). Whole-repository Ruff/format (632 files), full mypy (479 files), 164 policy tests, documentation paths and DOM-15 fixture checks passed.

CI-D1 result at `359d8d8f9f79236f466f83a5994842d2ffd2e5b1`, run `40775436714`: all 27 platform/Python jobs, PostgreSQL CLI/core, lint and coverage passed; all three Windows CLI jobs passed, with the retained Windows 3.14 evidence placing the installed-console test at 39.365 s (9 successful console calls, cleanup 9.775 s). That demonstrates intermittence, not the cause of the earlier roughly 90 s worker loss. PostgreSQL remaining job `123123611726` failed at `tests/system/test_measured_admission_pg.py` entering `WeftTestHarness`: the connection to isolated server 127.0.0.1:32769 closed unexpectedly after schema preflight; the isolated container (`weft-pg-test-2519-84dfc750`) had been deleted without retaining its logs. Dependent jobs skipped.

### CI-D2: retain isolated PostgreSQL evidence before deletion (2026-10-28 to 2026-10-29)

Class 4 cleanup-lifetime diagnostic; contract [TS-0]; no product behavior change; implementation deferred until the C2 measurements finished because `pytest_pg_runtime` was imported by the benchmark. Cause of the evidence gap: pytest 9.2's `_teardown_yield_fixture` resumes a yield fixture with `next(it)`, never `throw()`, so the fixture's `except` sees only setup failures and `finally` removed the container. Design: `isolated_postgres_server` gains an `else` clause (runs on normal resumption, including after a failed test call) that calls an opt-in collector in `bin/pytest-pg` before the unchanged `finally` cleanup; the collector appends to `postgres-<container>.log` in binary mode, runs `docker inspect` with only `State.Status/ExitCode/OOMKilled/Error/StartedAt/FinishedAt` first and then `docker logs --timestamps --tail 2000`, under one documented 10 s monotonic budget, flushing metadata before each child write and containing expected I/O/subprocess failures. The PostgreSQL workflow step enables the diagnostics directory and always uploads `diagnostics-postgres-${{ matrix.test_slice }}`. Tests use a real child pytest session through a loader wrapper that replaces only container start, schema initialization and the module-local subprocess dependency.

| Round | Reviewer / duration | Verdict | Dispositions |
|---|---|---|---|
| Plan review | Claude, 253.61 s | **PASS** | F1 accept `try/except/else/finally` instead of a flag; F2 accept drop `record_phase` (header uses `PYTEST_CURRENT_TEST` and a timestamp); F3 accept explicit loader-wrapper seam and module-local subprocess stub; F4 accept deterministic child env (pop `PYTEST_ADDOPTS`, set `PYTEST_XDIST_AUTO_NUM_WORKERS`); F5 accept unexpected-command witness, no teardown errors, Docker-like missing-token result; F6 accept binary append, raw descriptor writes, flush-order oracle; F7 accept isolated clock control and inspect-first; F8 accept single matrix step wording, filename, documented `Final` budget, plugin-owned directory. |
| Implementation | Claude, 201.52 s | **No blocker**; no P1/P2 | O1 (relative diagnostics path) no change; O2/O3 cosmetic declined; no scope expansion to outer-container logging or legacy cleanup timeouts. |
| Fresh-eyes implementation | same-family (did not author CI-D2) | No actionable findings | — |

Evidence: the child-pytest RED failed at the missing artifact after original-failure and cleanup guards (1 failed, 1.25 s); a first malformed after-delete mutant hit SyntaxError and is unqualified; the corrected mutant failed on the missing raw server token (1 failed, 1.21 s); restored focused checks 43 passed (2.41 s). Retained B tests: 29 SQLite cases (21.94 s), 34 PostgreSQL cases (44.24 s) with 16 real isolated-server artifacts, all showing selected State, readiness logs and both Docker commands returning 0.

Final local integration gate (2026-10-29): full SQLite 5,987 passed, 51 skipped (296 s); final retained-test simplifications 29 passed (23.63 s); full PostgreSQL 5,938 passed, 29 skipped (409 s), 12 workers, native capacity 300, diagnostics enabled; mypy (480 files), Ruff, format (633 files), suppression index and documentation paths passed.

### Final-head CI result and retained evidence (2026-10-29)

Head `b4afe36e1c56ce4c3448b0b4fff69563e69221fb`, Actions run `40890367466`, completed with failure. All 27 OS/Python jobs, PostgreSQL CLI/remaining, lint and coverage passed. PostgreSQL core-commands job `123505625501` failed during setup of `test_manager_services_control_while_real_launch_receipt_is_held[PING]`: the connection to the isolated server on port 32781 was rejected with `FATAL: the database system is starting up`, before any PING assertion. Four dependent extension/integration jobs were skipped, not passed. No rerun or behavior change. The new CI-D2 artifact `postgres-weft-pg-test-2962-25cf2916.log` showed the container running, exit 0, `OOMKilled=false`, normal temporary initialization-server shutdown, final server starting at 14:30:25.052 UTC, the rejected connection at 14:30:25.064 UTC and final ready at 14:30:25.070 UTC (6 ms later): startup-boundary evidence, not an OOM/crash attribution. It does not retroactively explain the earlier Windows and PG failures.

### CI-D3: PostgreSQL startup must prove the consumer endpoint is queryable

Class 4 harness startup-boundary repair; contract `08-Testing_Strategy.md` [TS-0]; base `b4afe36e`; no Manager/admission behavior change. Confirmed evidence: a scratch probe held the official `postgres:18` image at an init-script barrier; `_wait_for_postgres` returned (port 33156) while an actual host psycopg `SELECT 1` failed with server closed (`AssertionError: READINESS FALSE POSITIVE: helper returned while actual host query failed`). Readiness had combined a host TCP accept with container-local psql over the Unix socket, which can reach the entrypoint's temporary server. Separately, the real `_initialize_broker_schema` against a bound non-listening port (49217) returned success: the preflight was lazy. Ranked hypotheses: (1) mismatched readiness endpoints plus lazy preflight (reproduced); (2) stale/wrong environment target (no override observed); (3) abnormal restart/OOM (no evidence); (4) transient final-server startup (log confirms, but fails only because readiness proof was insufficient).

#### CI-D3 reviewed correction: simpler endpoint-proof split

The first design (an authenticated host psycopg query per readiness poll through `uv`) was superseded by the plan review's simpler alternative. Cross-family plan review: Claude, 311.93 s, **PASS**, six P2 findings. F1 accepted: remove the weak host-socket helper/import and add `-h 127.0.0.1` to the existing container psql query; the official image's temporary initialization server exposes only Unix sockets, so an in-container loopback TCP `SELECT 1` proves the final server is queryable. Then force `queue.peek_one()` in the existing host-DSN schema preflight inside `try/finally` before close. Together these prove final-server readiness and the actual consumer endpoint before any fixture uses it. Host forwarding or authentication failure therefore surfaces at the existing preflight, not in a new retry layer — but not immediately: `_initialize_broker_schema` surfaces host/auth failures before pytest starts only after simplebroker's 3-attempt connection retry against simplebroker-pg's 30 s pool checkout timeout (about 96 s; the native Linux run below recorded 96.29 s for the negative test). (Corrected 2026-10-29; the original text said the failure "surfaces immediately in the existing preflight". `2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md` later removed that 96 s negative test and replaced the 100 Hz `_PipeAdapter` loop with `_open_process_io`; see Closure.) No new uv polling, libpq routing cleanup, credential handling, query helper, deadline, clock or subprocess timeout machinery; the existing 60 s readiness deadline, 1 s retry and startup cleanup retained. F2 accepted: the Docker barrier regression asserts the old false-proof preconditions while initialization is held, then requires the readiness helper's RuntimeError attributable to TCP query refusal, using the production-default wait after release with a test-owned container and one small init-script barrier (no production parameter). F3/F4/F5 no longer apply (the uv-per-poll design was removed); no ambient libpq environment scrub. F6 accepted: the positive control verifies the default schema's `meta` table after the actual preflight, separately from the negative no-listener proof. Optional clock/sleep changes declined; existing credential logging is not expanded. Author fresh-eyes: the readiness test distinguishes the temporary from the final server through the protocol, not timing guesses.

#### CI-D3 schema-target amendment discovered by independent positive control

Initial GREEN rejected held bootstrap, but both schema assertions stayed red (34 passed, 2 failed, 8.11 s): `Queue`'s explicit string `db_path` is a SQLite filename, so the old preflight had two defects, lazy constructor/close and wrong backend binding (the forced peek alone created a local SQLite tree named `postgresql:/`; only this run's two generated directories were removed). Amendment: omit `db_path` so the public `Queue(name, persistent=True, config=resolve_config(env=os.environ))` resolves the canonical target through `target_for_directory`, the same route Weft consumers use. Targeted review: Claude, 122.48 s, **PASS**; F1 [P2] wording accepted (a repo-root `.broker.toml` or `BROKER_PROJECT_CONFIG_*` outranks the env target, with the same precedence as `weft/context.py`, and is detected by the unavailable-host and meta-table oracles; no new scrubbing). Separate author-independent fresh-eyes: **PASS**.

Implementation proof: qualified RED 2 failed (3.73 s) at DID NOT RAISE RuntimeError (after real host-socket plus Unix SQL bootstrap premises) and DID NOT RAISE CalledProcessError (bound non-listener). After the configured-default correction all 36 helper tests passed (97.16 s, 12 workers), including the real Docker held-bootstrap/release/default-readiness/meta proof and the real unavailable-host error; the unavailable-host negative took roughly 90 s through existing backend connection-failure behavior. The omit-peek mutation failed DID NOT RAISE CalledProcessError (1.08 s); the remove-`-h` mutation failed DID NOT RAISE RuntimeError (3.75 s).

### CI-D3 implementation review and portable regression closure

| Round | Reviewer / duration | Verdict | Dispositions |
|---|---|---|---|
| Implementation review | Claude, 149.36 s | Blocker F1 | F1 [P1] accept: native Linux postgres UID 999 cannot write pytest's mode-0700 barrier directory; use a dedicated test-owned mode-0777 bind root. F2 [P2] optional `with Queue(...)` rewrite declined (the small `try/finally` owns the lifetime correctly). F3 [P2] wording accepted: [TS-0] says in-container loopback TCP, followed by host preflight. |
| Native fresh-eyes | same-family | Findings accepted | Docker allocation moved inside the existing cleanup `try/finally`; the mandatory published-host socket premise removed (native Linux DNAT can correctly refuse during Unix-only bootstrap), so the macOS original RED remains a conditional witness and the portable regression proves Unix SQL success, held initialization and rejection by the corrected owner. |
| Implementation closure | Claude, 86.81 s | **No blocker** | F1 fixed; nothing new affecting Linux correctness. |
| Native final closure | same-family, author-independent | **PASS** | No remaining actionable finding. |

Portable final regression passed (1 test, 7.98 s); removing only `psql -h` failed the exact RuntimeError DID NOT RAISE assertion (3.62 s); full SQLite before these PG-only fixture corrections 5,987 passed, 53 skipped (211.04 s); full Ruff, format (633 files) and mypy (480 files) passed. Final full PostgreSQL: 5,940 passed, 29 skipped (363.02 s, 12 workers), with both the readiness regression and the unavailable-host preflight control executed.

CI-D3 exact-head completion: commit `540707a370d32cf9475832099fabd52fa1dc7f0a`, Actions run https://github.com/VanL/weft/actions/runs/40902305487, completed SUCCESS. All 36 jobs succeeded: 27 OS/Python matrix jobs, 3 PostgreSQL slices, lint, coverage and the 4 dependent Django/Docker/macOS sandbox/microsandbox jobs; no skipped job was counted as a pass. The b4afe PG core-commands failure is repaired with deterministic boundary RED and mutation evidence plus native Linux CI; this does not establish the cause of the earlier intermittent Windows failure. No rerun, merge, concurrency, load or timeout change. Native Linux firing evidence: PostgreSQL remaining job `123547973479` recorded call durations for `test_schema_preflight_requires_real_backend_operation` (96.29 s) and `test_postgres_readiness_rejects_bootstrap_then_initializes_schema` (6.89 s), with 2,192 passed and 22 skipped in that job; both regressions executed rather than skipped.

### Closure (2026-10-29)

Closed as `completed` by `2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md` Task 4: the repair work is on the branch and CI-verified at `540707a3` (all 36 jobs green). Later dispositions by that plan, recorded here for traceability: it replaced the 100 Hz `_PipeAdapter` polling loop with `_open_process_io` (a POSIX selector leaf with a pidfd/kqueue child-exit source and a Windows overlapped-I/O leaf, created through `open_supervised_process(...)`), retiring `RUFF-SUP-354`; and it removed the roughly 96 s negative test `test_schema_preflight_requires_real_backend_operation`, keeping `test_postgres_readiness_rejects_bootstrap_then_initializes_schema` as the positive schema proof. The causes of the intermittent Windows 3.14 autostart failure, the earlier 359d PostgreSQL unexpected close and the roughly 90 s Windows CLI worker loss remain undetermined.
