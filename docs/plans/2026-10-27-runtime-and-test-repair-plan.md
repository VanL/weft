# Runtime Ownership and Test Evidence Repairs

Status: draft
Source specs: docs/specifications/06-Resource_Management.md [RM-5.1], [RM-5.2]; docs/specifications/03-Manager_Architecture.md [MA-1.8]; docs/specifications/07-System_Invariants.md [STATE.1]–[STATE.6], [REDUCER.1]–[REDUCER.4]; docs/specifications/13-Agent_Runtime.md [AR-7]; docs/specifications/08-Testing_Strategy.md [TS-0], [TS-2]
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
Bounded drain must also release this runner's adapters and descriptors. Returning
while daemon threads remain stuck in buffered I/O is not a fix. The resource
monitor's psutil-visible-tree boundary remains unchanged; this plan does not
promise to kill already reparented or deliberately detached descendants.

Queue truth, reserved-policy handling, immutable `spec`/`io`, TID identity,
forward-only states, spawn-based broker isolation, and runtime-only state queues
remain unchanged. Keep the real
`TaskSpec -> Consumer -> TaskRunner -> outbox/task log` path in end-to-end proof.
Do not add a second state store, queue reader, hidden provider probe, timeout
polling cap to the steady task reactor, dependency, public flag, or new outcome
schema. Outbox output remains the bytes/text captured within the drain boundary.

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
| Interactive exit notification is independent of stream EOF; shared command normal completion currently waits for EOF. | Unify the root-exit fact and bounded-drain policy. Reuse `_drain_streams_until_closed`; retain separate adapters and session behavior. |
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
> root completion is accepted, the supervisor drains captured output for the
> bounded private stream-drain budget even if descendants retain inherited
> stdout or stderr. Expiry of that budget does not convert the completed root
> outcome into an execution timeout. The supervisor then stops and joins its
> I/O adapters and closes their owned pipe endpoints. Output beyond this drain
> boundary is not promised. Incomplete EOF at the boundary is logged as a
> bounded diagnostic; it does not create a new terminal state or outcome field.
> Output drainage performs finite work per stream before returning to other
> streams and supervision; final-drain deadline checks also occur inside that
> work, so a continuously producing descendant cannot extend the budget. These
> bounds do not preempt an arbitrarily blocking user callback.

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
   over an unaccepted timeout. Bound stream teardown and preserve root status.
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
  one silently holding descriptors; the same return/resource bound must hold.
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
checks live, and accept root completion independently from EOF. Reuse
`_drain_streams_until_closed` and `SUBPROCESS_STREAM_DRAIN_TIMEOUT`; extend their
ownership integration rather than copying a second drain loop. The existing
`_drain_stream_queue` drains until empty and is not bounded under continuing
production. Give each queue drain a finite chunk/work budget, with any new
internal constant in `weft/_constants.py`, and also check the final-drain absolute
deadline inside that loop. Return to stderr and supervision between finite
batches; the main supervisor must use bounded drains too. Preserve ordered text,
callback behavior and exactly-once EOF markers without treating budget exhaustion
as EOF. A slow callback may consume time and must be followed by a deadline check;
this repair does not promise to interrupt arbitrary blocking callback code.

Add a deterministic helper control using a real queue and a bounded callback
that replenishes it: the drain must return with backlog remaining, and an expired
final deadline must stop further draining. Add real sustained-output child cases
proving timeout/cancellation checks still run and a completed root still finishes
while its descendant writes. Reverting only the finite inner bound must fail the
intended assertion. Adapt `test_stream_reader_normalizes_crlf_split_across_chunks`
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

### 3. Bound lazy heap retention without losing cheap meld (S1)

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

### 4. Repair and consolidate the demonstrated test gaps (T1–T4)

Files to modify: `tests/specs/taskspec/test_state_transitions.py`,
`tests/core/test_state_machines.py`, `tests/core/test_agent_validation.py`, and
`tests/commands/test_task_commands.py`. Read the actual validator,
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

The staged real-Manager gate used actual broker classification, exact competing reservations, production stale-hint removal, and normal process_once admission/launch. The observer queued a keyed PING immediately before production compaction. Warmup L=100 compacted H=201 to 100 in 0.107 ms with PONG observed in 3.004 ms; L=10,000 compacted H=20,001 to 10,000 in 5.422 ms with PONG observed in 7.434 ms, in the same turn. Exact source absence, reserved custody and hint deletion were checked before compaction; each case launched root, descendant tip and witness processes, with a completed witness/outbox and all six owned PIDs absent after cleanup. This is staged integration, not a natural 20,000-process workload or a universal latency bound. The first probe mistakenly registered its in-process Manager for harness termination and exited by SIGTERM without a result; it is retained as failed evidence. The corrected probe follows the existing explicit Manager-owner fixture and exited zero. Artifacts: `heap-manager-gate.py`, `.jsonl`, `.stderr`, plus preserved `.attempt1` files. The observed 5.4 ms pause and 7.4 ms control response support retaining local geometric compaction; larger buckets retain the disclosed linear pause risk.


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
